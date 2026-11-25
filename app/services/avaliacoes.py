"""Avaliação mútua (48h), reputação 70/20/10 e selo Novo até 5 serviços (regras AVA01, AVA02, IA04).

Regras:
- Cada parte de uma vaga concluída (`concluida`, `liberada` ou `liberada_parcial`) pode avaliar a
  outra parte uma única vez com nota de 1 a 5 e comentário opcional, dentro do prazo de 48h
  (`avaliacao_prazo_min`).
- A avaliação de um lado fica oculta (`visivel_em = None`) até que o outro lado também avalie ou o
  prazo de 48h encerre (revelada imediatamente na segunda avaliação ou pela varredura
  `revelacao_avaliacoes`).
- Reputação (de 0 a 5):
    Reputação = 0,70 * média_avaliações (4,0 neutra quando não há avaliações visíveis)
              + 0,20 * (5 * (1 - taxa_cancelamento_tardio_nos_últimos_20))
              + 0,10 * max(0, 5 - 2 * denúncias_procedentes)
- Selo Novo: ativo até o 5º serviço concluído; cai ao concluir o 6º serviço.
"""

from datetime import datetime, timedelta

from fastapi import status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.schemas.avaliacoes import (
    AvaliacaoDetalhe,
    AvaliacoesDaVaga,
    ReputacaoDetalhe,
)
from app.erros import erro_http
from app.models import (
    Advertencia,
    Avaliacao,
    Denuncia,
    EstadoDenuncia,
    EstadoVaga,
    PerfilContratante,
    PerfilProfissional,
    Usuario,
    Vaga,
)
from app.services import configuracao, pagamentos, sancoes
from app.services.varreduras import varredura
from app.tempo import agora, como_utc

MEDIA_NEUTRA = 4.0
NOTA_MAXIMA = 5.0
PESO_MEDIA = 0.70
PESO_CANCELAMENTOS = 0.20
PESO_DENUNCIAS = 0.10
PENALIDADE_POR_DENUNCIA = 2.0
JANELA_ULTIMOS_SERVICOS = 20

ESTADOS_AVALIAVEIS: frozenset[EstadoVaga] = frozenset(
    {
        EstadoVaga.CONCLUIDA,
        EstadoVaga.LIBERADA,
        EstadoVaga.LIBERADA_PARCIAL,
    }
)

# Estados em que a vaga representou um compromisso assumido pelo usuário para a janela dos 20.
ESTADOS_COMPROMISSO: frozenset[EstadoVaga] = frozenset(
    {
        EstadoVaga.ACEITA,
        EstadoVaga.AGUARDANDO_PAGAMENTO,
        EstadoVaga.CONTRATADA,
        EstadoVaga.EM_EXECUCAO,
        EstadoVaga.ENCERRADA,
        EstadoVaga.CONCLUIDA,
        EstadoVaga.LIBERADA,
        EstadoVaga.LIBERADA_PARCIAL,
        EstadoVaga.CANCELADA,
        EstadoVaga.FALTA,
        EstadoVaga.EM_DISPUTA,
        EstadoVaga.REEMBOLSADA,
    }
)


def _em_utc(instante: datetime | None) -> datetime | None:
    return como_utc(instante) if instante is not None else None


def referencia_conclusao(vaga: Vaga) -> datetime:
    """Instante base a partir do qual corre a janela de 48h de avaliação.

    Usa o fim previsto do serviço (`inicio_em + duracao_minutos`), alinhado às varreduras de
    pós-serviço do módulo de pagamento.
    """
    return pagamentos.fim_do_servico(vaga)


def prazo_avaliacao_vaga(db: Session, vaga: Vaga) -> datetime:
    """Data/hora limite para enviar avaliação na vaga (48h após a conclusão em produção)."""
    prazo_min = configuracao.get("avaliacao_prazo_min", db=db)
    return referencia_conclusao(vaga) + timedelta(minutes=prazo_min)


def _participantes_da_vaga(db: Session, vaga: Vaga) -> tuple[int | None, int | None]:
    """Devolve `(contratante_usuario_id, profissional_usuario_id)` da vaga."""
    contratante_uid = db.scalar(
        select(PerfilContratante.usuario_id).where(PerfilContratante.id == vaga.contratante_id)
    )
    profissional_uid = None
    if vaga.profissional_id is not None:
        profissional_uid = db.scalar(
            select(PerfilProfissional.usuario_id).where(
                PerfilProfissional.id == vaga.profissional_id
            )
        )
    return contratante_uid, profissional_uid


def _avaliacao_esta_visivel(avaliacao: Avaliacao, instante: datetime | None = None) -> bool:
    if avaliacao.visivel_em is None:
        return False
    ref = instante or agora()
    return como_utc(avaliacao.visivel_em) <= ref


def montar_detalhe(avaliacao: Avaliacao) -> AvaliacaoDetalhe:
    visivel = _avaliacao_esta_visivel(avaliacao)
    return AvaliacaoDetalhe(
        id=avaliacao.id,
        vaga_id=avaliacao.vaga_id,
        avaliador_id=avaliacao.avaliador_id,
        avaliado_id=avaliacao.avaliado_id,
        nota=avaliacao.nota,
        comentario=avaliacao.comentario,
        criado_em=_em_utc(avaliacao.criado_em),
        visivel_em=_em_utc(avaliacao.visivel_em),
        visivel=visivel,
    )


def _revelar_se_prazo_vencido(db: Session, vaga: Vaga, avaliacoes: list[Avaliacao]) -> bool:
    """Se o prazo de 48h já encerrou, revela imediatamente quaisquer avaliações pendentes."""
    instante = agora()
    limite = prazo_avaliacao_vaga(db, vaga)
    if instante < limite:
        return False
    alterou = False
    for item in avaliacoes:
        if item.visivel_em is None:
            item.visivel_em = instante
            alterou = True
    if alterou:
        db.commit()
        for item in avaliacoes:
            db.refresh(item)
    return True


def registrar_avaliacao(
    db: Session,
    vaga: Vaga,
    avaliador: Usuario,
    *,
    nota: int,
    comentario: str | None = None,
) -> Avaliacao:
    """Registra a avaliação de um participante para o outro dentro da janela de 48h.

    Quando a outra parte ainda não avaliou, a avaliação nasce oculta (`visivel_em = None`).
    Quando ambos já avaliaram, as duas avaliações recebem `visivel_em = agora()` no mesmo ato.
    """
    contratante_uid, profissional_uid = _participantes_da_vaga(db, vaga)
    if avaliador.id not in (contratante_uid, profissional_uid):
        raise erro_http(
            status.HTTP_403_FORBIDDEN,
            "nao_participou_da_vaga",
            "Somente o contratante e o profissional contratado podem avaliar esta vaga.",
        )
    if vaga.estado not in ESTADOS_AVALIAVEIS:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "vaga_nao_avaliavel",
            "A avaliação só é permitida depois da conclusão do serviço.",
            estado_atual=vaga.estado.value,
        )
    if profissional_uid is None:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "vaga_sem_profissional",
            "A vaga não possui profissional contratado para avaliar.",
        )

    instante = agora()
    limite = prazo_avaliacao_vaga(db, vaga)
    if instante > limite:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "prazo_avaliacao_encerrado",
            "O prazo de 48 horas para avaliar este serviço já encerrou.",
            prazo_em=limite.isoformat(),
        )

    if not 1 <= nota <= 5:
        raise erro_http(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "nota_invalida",
            "A nota da avaliação deve ficar entre 1 e 5.",
        )

    existente = db.scalar(
        select(Avaliacao).where(
            Avaliacao.vaga_id == vaga.id,
            Avaliacao.avaliador_id == avaliador.id,
        )
    )
    if existente is not None:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "avaliacao_ja_registrada",
            "Você já avaliou este serviço.",
        )

    avaliado_id = profissional_uid if avaliador.id == contratante_uid else contratante_uid
    outra = db.scalar(
        select(Avaliacao).where(
            Avaliacao.vaga_id == vaga.id,
            Avaliacao.avaliador_id == avaliado_id,
        )
    )

    visivel_em = instante if outra is not None else None
    nova = Avaliacao(
        vaga_id=vaga.id,
        avaliador_id=avaliador.id,
        avaliado_id=avaliado_id,
        nota=nota,
        comentario=comentario.strip() if comentario and comentario.strip() else None,
        criado_em=instante,
        visivel_em=visivel_em,
    )
    if outra is not None and outra.visivel_em is None:
        outra.visivel_em = instante

    db.add(nova)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "avaliacao_ja_registrada",
            "Você já avaliou este serviço.",
        ) from None
    db.refresh(nova)
    for avaliado in {nova.avaliado_id, outra.avaliado_id if outra is not None else None}:
        alvo = db.get(Usuario, avaliado) if avaliado is not None else None
        if alvo is not None:
            sancoes.verificar_nota_baixa(db, alvo)
    return nova


def consultar_avaliacoes_da_vaga(
    db: Session,
    vaga: Vaga,
    usuario: Usuario | None = None,
) -> AvaliacoesDaVaga:
    """Retorna o estado das avaliações da vaga respeitando a ocultação até ambos ou o prazo."""
    todas = db.scalars(
        select(Avaliacao).where(Avaliacao.vaga_id == vaga.id).order_by(Avaliacao.id.asc())
    ).all()
    _revelar_se_prazo_vencido(db, vaga, todas)

    instante = agora()
    limite = prazo_avaliacao_vaga(db, vaga)
    ambos_avaliaram = len(todas) >= 2
    reveladas = (
        ambos_avaliaram
        or instante >= limite
        or any(_avaliacao_esta_visivel(item, instante) for item in todas)
    )

    minha = None
    recebida = None
    if usuario is not None:
        for item in todas:
            if item.avaliador_id == usuario.id:
                minha = montar_detalhe(item)
            elif item.avaliado_id == usuario.id and _avaliacao_esta_visivel(item, instante):
                recebida = montar_detalhe(item)

    publicas = [montar_detalhe(item) for item in todas if _avaliacao_esta_visivel(item, instante)]
    return AvaliacoesDaVaga(
        vaga_id=vaga.id,
        prazo_em=limite,
        reveladas=reveladas,
        minha_avaliacao=minha,
        avaliacao_recebida=recebida,
        avaliacoes=publicas,
    )


def listar_avaliacoes_visiveis_do_usuario(
    db: Session,
    usuario_id: int,
) -> list[AvaliacaoDetalhe]:
    """Lista todas as avaliações já reveladas recebidas por `usuario_id`."""
    instante = agora()
    itens = db.scalars(
        select(Avaliacao)
        .where(
            Avaliacao.avaliado_id == usuario_id,
            Avaliacao.visivel_em.is_not(None),
            Avaliacao.visivel_em <= instante,
        )
        .order_by(Avaliacao.id.desc())
    ).all()
    return [montar_detalhe(item) for item in itens]


@varredura("revelacao_avaliacoes")
def revelar_avaliacoes_por_prazo(db: Session) -> int:
    """Torna visíveis as avaliações unilaterais cujo prazo de 48h após a conclusão já venceu."""
    instante = agora()
    ocultas = db.scalars(select(Avaliacao).where(Avaliacao.visivel_em.is_(None))).all()
    if not ocultas:
        return 0

    reveladas = 0
    for avaliacao in ocultas:
        vaga = db.get(Vaga, avaliacao.vaga_id)
        if vaga is None:
            continue
        if instante >= prazo_avaliacao_vaga(db, vaga):
            avaliacao.visivel_em = instante
            reveladas += 1
    if reveladas:
        db.commit()
        for avaliado_id in {a.avaliado_id for a in ocultas if a.visivel_em is not None}:
            alvo = db.get(Usuario, avaliado_id)
            if alvo is not None:
                sancoes.verificar_nota_baixa(db, alvo)
    return reveladas


# ---------------------------------------------------------------------------
# Selo Novo (até 5 serviços concluídos; some ao concluir o 6º) e conclusão
# ---------------------------------------------------------------------------


def possui_selo_novo(
    servicos_concluidos: int,
    *,
    db: Session | None = None,
) -> bool:
    """Verdadeiro enquanto o profissional tiver até 5 serviços concluídos; cai no 6º (US55)."""
    limite = int(configuracao.get("servicos_para_deixar_de_ser_novo", db=db))
    return servicos_concluidos <= limite


def registrar_servico_concluido(
    db: Session, profissional: PerfilProfissional
) -> PerfilProfissional:
    """Incrementa os serviços concluídos do profissional; no 6º serviço o selo Novo some."""
    profissional.servicos_concluidos = (profissional.servicos_concluidos or 0) + 1
    db.commit()
    db.refresh(profissional)
    return profissional


# ---------------------------------------------------------------------------
# Cálculo da Reputação 70 / 20 / 10
# ---------------------------------------------------------------------------


def formula_reputacao(
    *,
    nota_media: float | None,
    taxa_cancelamento_tardio: float = 0.0,
    denuncias_procedentes: int = 0,
) -> tuple[float, float, float, float]:
    """Calcula a reputação 70/20/10 a partir dos indicadores e devolve:
    `(reputacao_final, media_considerada, nota_cancelamentos, nota_denuncias)`.

    - 70% média das avaliações (4,0 neutra quando `nota_media` é `None`).
    - 20% cancelamentos: `5 * (1 - taxa_cancelamento_tardio)`.
    - 10% denúncias: `max(0, 5 - 2 * denuncias_procedentes)`.
    """
    media = float(nota_media) if nota_media is not None else MEDIA_NEUTRA
    media = min(NOTA_MAXIMA, max(0.0, media))

    taxa = min(1.0, max(0.0, float(taxa_cancelamento_tardio)))
    nota_cancelamentos = NOTA_MAXIMA * (1.0 - taxa)

    procedentes = max(0, int(denuncias_procedentes))
    nota_denuncias = max(0.0, NOTA_MAXIMA - PENALIDADE_POR_DENUNCIA * procedentes)

    reputacao = (
        PESO_MEDIA * media
        + PESO_CANCELAMENTOS * nota_cancelamentos
        + PESO_DENUNCIAS * nota_denuncias
    )
    return (
        round(reputacao, 2),
        round(media, 2),
        round(nota_cancelamentos, 2),
        round(nota_denuncias, 2),
    )


def _taxa_cancelamento_tardio_ultimos_20(
    db: Session,
    usuario: Usuario,
) -> tuple[float, int, int]:
    """Devolve `(taxa, cancelamentos_tardios, amostra)` nos últimos 20 compromissos do usuário.

    Um cancelamento tardio é identificado por uma `Advertencia` do usuário na vaga (ou motivo
    contendo `cancelamento`) dentro dos 20 compromissos mais recentes, ou pelas advertências de
    cancelamento tardio caso não estejam vinculadas a vagas.
    """
    filtros = []
    if usuario.perfil_profissional is not None:
        filtros.append(Vaga.profissional_id == usuario.perfil_profissional.id)
    if usuario.perfil_contratante is not None:
        filtros.append(Vaga.contratante_id == usuario.perfil_contratante.id)

    ultimas_vagas_ids: list[int] = []
    if filtros:
        ultimas_vagas_ids = list(
            db.scalars(
                select(Vaga.id)
                .where(or_(*filtros), Vaga.estado.in_(ESTADOS_COMPROMISSO))
                .order_by(Vaga.id.desc())
                .limit(JANELA_ULTIMOS_SERVICOS)
            ).all()
        )

    advertencias = db.scalars(
        select(Advertencia)
        .where(Advertencia.usuario_id == usuario.id)
        .order_by(Advertencia.id.desc())
        .limit(JANELA_ULTIMOS_SERVICOS)
    ).all()

    if ultimas_vagas_ids:
        vagas_com_advertencia = {
            adv.vaga_id for adv in advertencias if adv.vaga_id in ultimas_vagas_ids
        }
        avulsas = sum(1 for adv in advertencias if adv.vaga_id is None)
        tardios = min(JANELA_ULTIMOS_SERVICOS, len(vagas_com_advertencia) + avulsas)
        # A taxa nos últimos 20 serviços é medida sobre a janela de 20 (ou sobre a amostra se
        # todos os compromissos forem explicitamente considerados na janela de 20).
        base = JANELA_ULTIMOS_SERVICOS
        return tardios / base, tardios, len(ultimas_vagas_ids)

    if advertencias:
        tardios = min(JANELA_ULTIMOS_SERVICOS, len(advertencias))
        return tardios / JANELA_ULTIMOS_SERVICOS, tardios, tardios

    return 0.0, 0, 0


def calcular_reputacao_usuario(db: Session, usuario: Usuario) -> ReputacaoDetalhe:
    """Calcula a reputação 70/20/10 e o estado do selo Novo para `usuario`."""
    instante = agora()
    media_db, total_db = db.execute(
        select(func.avg(Avaliacao.nota), func.count(Avaliacao.id)).where(
            Avaliacao.avaliado_id == usuario.id,
            Avaliacao.visivel_em.is_not(None),
            Avaliacao.visivel_em <= instante,
        )
    ).one()

    total_avaliacoes = int(total_db or 0)
    nota_media = float(media_db) if media_db is not None and total_avaliacoes > 0 else None

    taxa_canc, tardios, amostra = _taxa_cancelamento_tardio_ultimos_20(db, usuario)

    denuncias_procedentes = int(
        db.scalar(
            select(func.count(Denuncia.id)).where(
                Denuncia.denunciado_id == usuario.id,
                Denuncia.estado == EstadoDenuncia.PROCEDENTE,
            )
        )
        or 0
    )

    reputacao, media_considerada, nota_canc, nota_den = formula_reputacao(
        nota_media=nota_media,
        taxa_cancelamento_tardio=taxa_canc,
        denuncias_procedentes=denuncias_procedentes,
    )

    servicos = (
        usuario.perfil_profissional.servicos_concluidos
        if usuario.perfil_profissional is not None
        else 0
    )
    novo = possui_selo_novo(servicos, db=db)

    return ReputacaoDetalhe(
        usuario_id=usuario.id,
        reputacao=reputacao,
        nota_media=round(nota_media, 2) if nota_media is not None else None,
        media_considerada=media_considerada,
        total_avaliacoes=total_avaliacoes,
        nota_cancelamentos=nota_canc,
        taxa_cancelamento_tardio=round(taxa_canc, 4),
        cancelamentos_tardios_ultimos_20=tardios,
        amostra_compromissos=amostra,
        nota_denuncias=nota_den,
        denuncias_procedentes=denuncias_procedentes,
        servicos_concluidos=servicos,
        novo=novo,
    )
