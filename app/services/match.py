"""Match: seleção dos profissionais convidados para uma vaga.

O fluxo tem três etapas: `elegiveis` aplica os cortes de cadastro, distância, comprovação e
agenda; `pontuar` dá uma nota de 0 a 1 com os pesos da configuração; `selecionar_top3` ordena e
reserva uma posição para um profissional com selo Novo. `gerar_rodada` grava o resultado como
convites. Nenhum parâmetro de plano pago entra na seleção.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, contains_eager, selectinload

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
from app.services import configuracao, notificacoes
from app.services.geo import caixa_para_raio, distancia_km
from app.services.varreduras import varredura
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


def pendencias_da_categoria(
    db: Session, perfil: PerfilProfissional, categoria: Categoria
) -> list[str]:
    """O que falta aprovar para o profissional atuar na categoria, com as mesmas regras do match.

    Risco médio ou alto exige experiência aprovada; categorias que exigem qualificação ou EPI
    pedem a comprovação aprovada da própria categoria.
    """
    faltas: list[str] = []
    if (
        categoria.risco in RISCOS_COM_EXPERIENCIA
        and perfil.experiencia_aprovada != StatusAprovacao.APROVADO
    ):
        faltas.append("experiencia")
    for exigida, modelo, nome in (
        (categoria.exige_qualificacao, Qualificacao, "qualificacao"),
        (categoria.exige_epi, EpiProfissional, "epi"),
    ):
        if not exigida:
            continue
        aprovada = db.scalar(
            select(modelo.id).where(
                modelo.profissional_id == perfil.id,
                modelo.categoria_id == categoria.id,
                modelo.status == StatusAprovacao.APROVADO,
            )
        )
        if aprovada is None:
            faltas.append(nome)
    return faltas


def pode_atuar(db: Session, perfil: PerfilProfissional, categoria: Categoria) -> bool:
    """Regra única de aptidão por categoria (SEG01, SEG02, SEG04), do perfil e do match."""
    return not pendencias_da_categoria(db, perfil, categoria)


def _candidatos_elegiveis(db: Session, vaga: Vaga) -> list[tuple[PerfilProfissional, float]]:
    """Elegíveis com a distância até a vaga, em ordem de id.

    O endereço e as categorias dos candidatos vêm carregados junto da consulta, e a
    configuração é lida uma única vez, para que o custo em consultas não dependa do número de
    candidatos.
    """
    categoria = db.get(Categoria, vaga.categoria_id)
    contratante = db.get(PerfilContratante, vaga.contratante_id)
    local = db.get(Endereco, vaga.endereco_id)
    if local is None:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "vaga_sem_endereco",
            "A vaga precisa de um endereço para buscar profissionais.",
        )
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
        .options(
            contains_eager(PerfilProfissional.endereco),
            selectinload(PerfilProfissional.categorias),
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

    no_raio = []
    for candidato in db.scalars(consulta).unique().all():
        destino = candidato.endereco
        distancia = distancia_km(
            local.latitude, local.longitude, destino.latitude, destino.longitude
        )
        if distancia <= min(candidato.raio_km, raio_maximo):
            no_raio.append((candidato, distancia))
    ocupados = _profissionais_ocupados(db, vaga, [p.id for p, _ in no_raio])
    return [(p, d) for p, d in no_raio if p.id not in ocupados]


def elegiveis(db: Session, vaga: Vaga) -> list[PerfilProfissional]:
    """Profissionais aptos a receber convite para a vaga, em ordem de id."""
    return [perfil for perfil, _ in _candidatos_elegiveis(db, vaga)]


@dataclass(frozen=True)
class _Contexto:
    """Dados compartilhados por todos os candidatos de uma seleção, lidos de uma só vez."""

    vaga: Vaga
    local: Endereco
    raio_maximo: float
    pesos: dict[str, float]
    minimo_para_deixar_de_ser_novo: float
    experiencias_aprovadas: dict[int, int]
    reputacao: dict[int, float]
    historico_de_preco: dict[int, float]


def _mapa(db: Session, consulta) -> dict[int, float]:
    return {chave: float(valor) for chave, valor in db.execute(consulta).all() if valor is not None}


def _montar_contexto(db: Session, vaga: Vaga, profissionais: list[PerfilProfissional]) -> _Contexto:
    ids = [p.id for p in profissionais]
    usuarios = [p.usuario_id for p in profissionais]
    experiencias = _mapa(
        db,
        select(Experiencia.profissional_id, func.count(Experiencia.id))
        .where(
            Experiencia.profissional_id.in_(ids),
            Experiencia.categoria_id == vaga.categoria_id,
            Experiencia.status == StatusAprovacao.APROVADO,
        )
        .group_by(Experiencia.profissional_id),
    )
    reputacao = _mapa(
        db,
        select(Avaliacao.avaliado_id, func.avg(Avaliacao.nota))
        .where(
            Avaliacao.avaliado_id.in_(usuarios),
            Avaliacao.visivel_em.is_not(None),
            Avaliacao.visivel_em <= agora(),
        )
        .group_by(Avaliacao.avaliado_id),
    )
    historico = _mapa(
        db,
        select(Vaga.profissional_id, func.avg(Vaga.valor_fixado))
        .where(
            Vaga.profissional_id.in_(ids),
            Vaga.estado.in_(ESTADOS_LIBERADOS),
            Vaga.valor_fixado.is_not(None),
        )
        .group_by(Vaga.profissional_id),
    )
    return _Contexto(
        vaga=vaga,
        local=db.get(Endereco, vaga.endereco_id),
        raio_maximo=configuracao.get("raio_maximo_km", db=db),
        pesos={
            "localizacao": configuracao.get("peso_localizacao", db=db),
            "habilidades": configuracao.get("peso_habilidades", db=db),
            "reputacao": configuracao.get("peso_reputacao", db=db),
            "preco": configuracao.get("peso_preco", db=db),
        },
        minimo_para_deixar_de_ser_novo=configuracao.get("servicos_para_deixar_de_ser_novo", db=db),
        experiencias_aprovadas={int(k): int(v) for k, v in experiencias.items()},
        reputacao=reputacao,
        historico_de_preco=historico,
    )


def _nota_localizacao(ctx: _Contexto, profissional: PerfilProfissional) -> float:
    destino = profissional.endereco
    distancia = distancia_km(
        ctx.local.latitude, ctx.local.longitude, destino.latitude, destino.longitude
    )
    raio = min(profissional.raio_km, ctx.raio_maximo)
    if raio <= 0:
        return 1.0 if distancia == 0 else 0.0
    return max(0.0, 1.0 - distancia / raio)


def _nota_habilidades(ctx: _Contexto, profissional: PerfilProfissional) -> float:
    if all(c.id != ctx.vaga.categoria_id for c in profissional.categorias):
        return 0.0
    aprovadas = ctx.experiencias_aprovadas.get(profissional.id, 0)
    return 0.5 + 0.5 * min(aprovadas, EXPERIENCIAS_SATURACAO) / EXPERIENCIAS_SATURACAO


def _nota_reputacao(ctx: _Contexto, profissional: PerfilProfissional) -> float:
    media = ctx.reputacao.get(profissional.usuario_id, NOTA_SEM_AVALIACAO)
    return media / NOTA_MAXIMA


def _nota_preco(ctx: _Contexto, profissional: PerfilProfissional) -> float:
    historico = ctx.historico_de_preco.get(profissional.id)
    if historico is None:
        return 1.0
    vaga = ctx.vaga
    valor_vaga = vaga.valor_fixado if vaga.valor_fixado is not None else vaga.valor_inicial
    a, b = float(valor_vaga), float(historico)
    maior = max(a, b)
    if maior <= 0:
        return 1.0
    return 1.0 - min(1.0, abs(a - b) / maior)


def _pontuar(ctx: _Contexto, profissional: PerfilProfissional) -> float:
    notas = {
        "localizacao": _nota_localizacao(ctx, profissional),
        "habilidades": _nota_habilidades(ctx, profissional),
        "reputacao": _nota_reputacao(ctx, profissional),
        "preco": _nota_preco(ctx, profissional),
    }
    soma_pesos = sum(ctx.pesos.values())
    if soma_pesos <= 0:
        return 0.0
    total = sum(ctx.pesos[c] * notas[c] for c in ctx.pesos) / soma_pesos
    return min(1.0, max(0.0, total))


def pontuar(db: Session, vaga: Vaga, profissional: PerfilProfissional) -> float:
    """Nota de 0 a 1 do profissional para a vaga, com os pesos da configuração.

    Os pesos são normalizados pela soma para que a nota continue entre 0 e 1 mesmo que a
    configuração seja alterada para valores que não somem 1.
    """
    return _pontuar(_montar_contexto(db, vaga, [profissional]), profissional)


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
    elegidos = _candidatos_elegiveis(db, vaga)
    if not elegidos:
        return []
    ctx = _montar_contexto(db, vaga, [perfil for perfil, _ in elegidos])

    def novo(candidato: _Candidato) -> bool:
        return candidato.perfil.servicos_concluidos < ctx.minimo_para_deixar_de_ser_novo

    candidatos = sorted(
        (_Candidato(perfil, _pontuar(ctx, perfil), distancia) for perfil, distancia in elegidos),
        key=lambda c: c.ordem,
    )
    if len(candidatos) <= 3:
        escolhidos = candidatos
    else:
        escolhidos = candidatos[:2]
        restantes = candidatos[2:]
        if any(novo(c) for c in escolhidos):
            escolhidos.append(restantes[0])
        else:
            escolhidos.append(next((c for c in restantes if novo(c)), restantes[0]))
        escolhidos.sort(key=lambda c: c.ordem)
    return [(c.perfil, c.pontuacao) for c in escolhidos]


def gerar_rodada(db: Session, vaga: Vaga) -> list[Convite]:
    """Cria os convites de uma rodada para a vaga e grava no banco.

    A primeira rodada usa `vaga.rodada`; as seguintes incrementam o contador. Uma rodada só
    pode ser gerada quando nenhum convite da rodada atual continua aguardando resposta.
    Sem elegíveis, nada é alterado e a lista devolvida é vazia. Só vagas publicadas recebem
    novos convites.
    """
    if vaga.estado != EstadoVaga.PUBLICADA:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "vaga_indisponivel",
            "Só vagas publicadas recebem novos convites.",
            estado_atual=vaga.estado.value,
        )
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
    try:
        db.commit()
    except IntegrityError:
        # Outra requisição gerou a rodada ao mesmo tempo para os mesmos profissionais.
        db.rollback()
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "rodada_em_andamento",
            "A rodada desta vaga já foi gerada.",
        ) from None
    for convite in convites:
        db.refresh(convite)
    notificacoes.notificar_selecionados_top3(db, vaga, convites)
    return convites


@varredura("expiracao_convites")
def expirar_convites_vencidos(db: Session) -> int:
    """Expira convites escolhidos cujo prazo de aceite acabou e reabre a vaga para o match."""
    vencidos = db.scalars(
        select(Convite).where(
            Convite.estado == EstadoConvite.ESCOLHIDO,
            Convite.expira_em.is_not(None),
            Convite.expira_em <= agora(),
        )
    ).all()
    for convite in vencidos:
        convite.estado = EstadoConvite.EXPIRADO
        vaga = db.get(Vaga, convite.vaga_id)
        if vaga.estado == EstadoVaga.AGUARDANDO_ACEITE:
            from app.services import vagas as vagas_servico

            vagas_servico.transicionar(
                vaga,
                vagas_servico.EventoVaga.EXPIRAR_CONVITE,
                motivo="convite_escolhido_expirado",
            )
            vaga.prazo_em = vagas_servico.calcular_prazo_publicacao(db, vaga.inicio_em)
    if vencidos:
        db.commit()
    return len(vencidos)
