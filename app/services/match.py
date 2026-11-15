"""Match: seleção dos profissionais convidados para uma vaga.

O fluxo tem três etapas: `elegiveis` aplica os cortes de cadastro, distância, comprovação e
agenda; `pontuar` dá uma nota de 0 a 1 com os pesos da configuração; `selecionar_top3` ordena e
reserva uma posição para um profissional com selo Novo. `gerar_rodada` grava o resultado como
convites. Nenhum parâmetro de plano pago entra na seleção.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.erros import erro_http
from app.models import (
    Avaliacao,
    Categoria,
    Convite,
    Endereco,
    EpiProfissional,
    EstadoConta,
    EstadoConvite,
    EstadoVaga,
    Experiencia,
    PerfilContratante,
    PerfilProfissional,
    Qualificacao,
    RiscoCategoria,
    StatusAprovacao,
    Usuario,
    Vaga,
    categorias_profissional,
)
from app.services import configuracao
from app.services.geo import caixa_para_raio, distancia_km
from app.tempo import agora, como_utc

# Estados em que a agenda do profissional está comprometida com outra vaga.
ESTADOS_OCUPADOS = (
    EstadoVaga.ACEITA,
    EstadoVaga.AGUARDANDO_PAGAMENTO,
    EstadoVaga.CONTRATADA,
    EstadoVaga.EM_EXECUCAO,
)

# Estados em que a vaga contou como histórico de preço do profissional.
ESTADOS_LIBERADOS = (EstadoVaga.LIBERADA, EstadoVaga.LIBERADA_PARCIAL)

# Convites que ainda aguardam resposta e, portanto, mantêm a rodada aberta.
ESTADOS_CONVITE_ABERTO = (
    EstadoConvite.SUGERIDO,
    EstadoConvite.INTERESSADO,
    EstadoConvite.ESCOLHIDO,
)

RISCOS_COM_EXPERIENCIA = (RiscoCategoria.MEDIO, RiscoCategoria.ALTO)

EXPERIENCIAS_SATURACAO = 5
NOTA_MAXIMA = 5
NOTA_SEM_AVALIACAO = 4.0


def _intervalo(vaga: Vaga) -> tuple[datetime, datetime]:
    inicio = como_utc(vaga.inicio_em)
    return inicio, inicio + timedelta(minutes=vaga.duracao_minutos)


def raio_efetivo_km(db: Session, profissional: PerfilProfissional) -> float:
    """Raio do profissional limitado ao máximo permitido pela configuração."""
    return min(profissional.raio_km, configuracao.get("raio_maximo_km", db=db))


def distancia_ate_vaga(db: Session, vaga: Vaga, profissional: PerfilProfissional) -> float:
    """Distância em km entre o endereço da vaga e o endereço do profissional."""
    origem = db.get(Endereco, vaga.endereco_id)
    destino = profissional.endereco
    return distancia_km(origem.latitude, origem.longitude, destino.latitude, destino.longitude)


def _profissionais_ocupados(db: Session, vaga: Vaga, ids: list[int]) -> set[int]:
    """Profissionais com outra vaga comprometida que se sobrepõe ao horário desta."""
    if not ids:
        return set()
    inicio, fim = _intervalo(vaga)
    outras = db.scalars(
        select(Vaga).where(
            Vaga.id != vaga.id,
            Vaga.profissional_id.in_(ids),
            Vaga.estado.in_(ESTADOS_OCUPADOS),
        )
    )
    ocupados = set()
    for outra in outras:
        outro_inicio, outro_fim = _intervalo(outra)
        if outro_inicio < fim and outro_fim > inicio:
            ocupados.add(outra.profissional_id)
    return ocupados


def elegiveis(db: Session, vaga: Vaga) -> list[PerfilProfissional]:
    """Profissionais aptos a receber convite para a vaga, em ordem de id."""
    categoria = db.get(Categoria, vaga.categoria_id)
    contratante = db.get(PerfilContratante, vaga.contratante_id)
    local = db.get(Endereco, vaga.endereco_id)
    raio_maximo = configuracao.get("raio_maximo_km", db=db)
    caixa = caixa_para_raio(local.latitude, local.longitude, raio_maximo)

    ja_convidado = (
        select(Convite.id)
        .where(Convite.vaga_id == vaga.id, Convite.profissional_id == PerfilProfissional.id)
        .exists()
    )
    consulta = (
        select(PerfilProfissional)
        .join(Usuario, Usuario.id == PerfilProfissional.usuario_id)
        .join(Endereco, Endereco.id == PerfilProfissional.endereco_id)
        .join(
            categorias_profissional,
            categorias_profissional.c.profissional_id == PerfilProfissional.id,
        )
        .where(
            Usuario.estado == EstadoConta.ATIVA,
            PerfilProfissional.verificacao == StatusAprovacao.APROVADO,
            PerfilProfissional.usuario_id != contratante.usuario_id,
            categorias_profissional.c.categoria_id == vaga.categoria_id,
            Endereco.latitude.between(caixa.lat_min, caixa.lat_max),
            Endereco.longitude.between(caixa.lon_min, caixa.lon_max),
            ~ja_convidado,
        )
        .order_by(PerfilProfissional.id)
    )
    if categoria.risco in RISCOS_COM_EXPERIENCIA:
        consulta = consulta.where(
            PerfilProfissional.experiencia_aprovada == StatusAprovacao.APROVADO
        )
    if categoria.exige_qualificacao:
        consulta = consulta.where(
            select(Qualificacao.id)
            .where(
                Qualificacao.profissional_id == PerfilProfissional.id,
                Qualificacao.categoria_id == categoria.id,
                Qualificacao.status == StatusAprovacao.APROVADO,
            )
            .exists()
        )
    if categoria.exige_epi:
        consulta = consulta.where(
            select(EpiProfissional.id)
            .where(
                EpiProfissional.profissional_id == PerfilProfissional.id,
                EpiProfissional.categoria_id == categoria.id,
                EpiProfissional.status == StatusAprovacao.APROVADO,
            )
            .exists()
        )

    candidatos = db.scalars(consulta).all()
    no_raio = [p for p in candidatos if distancia_ate_vaga(db, vaga, p) <= raio_efetivo_km(db, p)]
    ocupados = _profissionais_ocupados(db, vaga, [p.id for p in no_raio])
    return [p for p in no_raio if p.id not in ocupados]


def _nota_localizacao(db: Session, vaga: Vaga, profissional: PerfilProfissional) -> float:
    raio = raio_efetivo_km(db, profissional)
    distancia = distancia_ate_vaga(db, vaga, profissional)
    if raio <= 0:
        return 1.0 if distancia == 0 else 0.0
    return max(0.0, 1.0 - distancia / raio)


def _nota_habilidades(db: Session, vaga: Vaga, profissional: PerfilProfissional) -> float:
    if all(c.id != vaga.categoria_id for c in profissional.categorias):
        return 0.0
    aprovadas = db.scalar(
        select(func.count(Experiencia.id)).where(
            Experiencia.profissional_id == profissional.id,
            Experiencia.categoria_id == vaga.categoria_id,
            Experiencia.status == StatusAprovacao.APROVADO,
        )
    )
    return 0.5 + 0.5 * min(aprovadas, EXPERIENCIAS_SATURACAO) / EXPERIENCIAS_SATURACAO


def _nota_reputacao(db: Session, profissional: PerfilProfissional) -> float:
    media = db.scalar(
        select(func.avg(Avaliacao.nota)).where(
            Avaliacao.avaliado_id == profissional.usuario_id,
            Avaliacao.visivel_em.is_not(None),
            Avaliacao.visivel_em <= agora(),
        )
    )
    if media is None:
        media = NOTA_SEM_AVALIACAO
    return float(media) / NOTA_MAXIMA


def _nota_preco(db: Session, vaga: Vaga, profissional: PerfilProfissional) -> float:
    historico = db.scalar(
        select(func.avg(Vaga.valor_fixado)).where(
            Vaga.profissional_id == profissional.id,
            Vaga.estado.in_(ESTADOS_LIBERADOS),
            Vaga.valor_fixado.is_not(None),
        )
    )
    if historico is None:
        return 1.0
    valor_vaga = vaga.valor_fixado if vaga.valor_fixado is not None else vaga.valor_inicial
    a, b = float(valor_vaga), float(Decimal(str(historico)))
    maior = max(a, b)
    if maior <= 0:
        return 1.0
    return 1.0 - min(1.0, abs(a - b) / maior)


def pontuar(db: Session, vaga: Vaga, profissional: PerfilProfissional) -> float:
    """Nota de 0 a 1 do profissional para a vaga, com os pesos da configuração.

    Os pesos são normalizados pela soma para que a nota continue entre 0 e 1 mesmo que a
    configuração seja alterada para valores que não somem 1.
    """
    pesos = {
        "localizacao": configuracao.get("peso_localizacao", db=db),
        "habilidades": configuracao.get("peso_habilidades", db=db),
        "reputacao": configuracao.get("peso_reputacao", db=db),
        "preco": configuracao.get("peso_preco", db=db),
    }
    notas = {
        "localizacao": _nota_localizacao(db, vaga, profissional),
        "habilidades": _nota_habilidades(db, vaga, profissional),
        "reputacao": _nota_reputacao(db, profissional),
        "preco": _nota_preco(db, vaga, profissional),
    }
    soma_pesos = sum(pesos.values())
    if soma_pesos <= 0:
        return 0.0
    total = sum(pesos[c] * notas[c] for c in pesos) / soma_pesos
    return min(1.0, max(0.0, total))


@dataclass(frozen=True)
class _Candidato:
    perfil: PerfilProfissional
    pontuacao: float
    distancia: float

    @property
    def ordem(self) -> tuple[float, float, int]:
        return (-self.pontuacao, self.distancia, self.perfil.id)


def eh_novo(db: Session, profissional: PerfilProfissional) -> bool:
    """Profissional com selo Novo: ainda não atingiu o mínimo de serviços concluídos."""
    minimo = configuracao.get("servicos_para_deixar_de_ser_novo", db=db)
    return profissional.servicos_concluidos < minimo


def selecionar_top3(db: Session, vaga: Vaga) -> list[tuple[PerfilProfissional, float]]:
    """Os dois melhores elegíveis mais o melhor profissional Novo, ordenados por pontuação.

    Desempate por menor distância e depois por menor id. Se os dois melhores já incluem um
    Novo, ou se não há nenhum Novo elegível, a terceira vaga vai para o terceiro melhor geral.
    """
    candidatos = sorted(
        (
            _Candidato(p, pontuar(db, vaga, p), distancia_ate_vaga(db, vaga, p))
            for p in elegiveis(db, vaga)
        ),
        key=lambda c: c.ordem,
    )
    if len(candidatos) <= 3:
        escolhidos = candidatos
    else:
        escolhidos = candidatos[:2]
        restantes = candidatos[2:]
        if any(eh_novo(db, c.perfil) for c in escolhidos):
            escolhidos.append(restantes[0])
        else:
            novo = next((c for c in restantes if eh_novo(db, c.perfil)), restantes[0])
            escolhidos.append(novo)
        escolhidos.sort(key=lambda c: c.ordem)
    return [(c.perfil, c.pontuacao) for c in escolhidos]


def gerar_rodada(db: Session, vaga: Vaga) -> list[Convite]:
    """Cria os convites de uma rodada para a vaga e grava no banco.

    A primeira rodada usa `vaga.rodada`; as seguintes incrementam o contador. Uma rodada só
    pode ser gerada quando nenhum convite da rodada atual continua aguardando resposta.
    Sem elegíveis, nada é alterado e a lista devolvida é vazia.
    """
    convites_existentes = db.scalars(select(Convite).where(Convite.vaga_id == vaga.id)).all()
    em_andamento = [
        c
        for c in convites_existentes
        if c.rodada == vaga.rodada and c.estado in ESTADOS_CONVITE_ABERTO
    ]
    if em_andamento:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "rodada_em_andamento",
            "A rodada atual ainda tem convites aguardando resposta.",
            rodada=vaga.rodada,
        )

    selecionados = selecionar_top3(db, vaga)
    if not selecionados:
        return []

    rodada = vaga.rodada if not convites_existentes else vaga.rodada + 1
    vaga.rodada = rodada
    convites = [
        Convite(
            vaga_id=vaga.id,
            profissional_id=perfil.id,
            rodada=rodada,
            posicao=posicao,
            pontuacao=pontuacao,
            estado=EstadoConvite.SUGERIDO,
        )
        for posicao, (perfil, pontuacao) in enumerate(selecionados, start=1)
    ]
    db.add_all(convites)
    db.commit()
    for convite in convites:
        db.refresh(convite)
    return convites
