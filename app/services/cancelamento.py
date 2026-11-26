"""Cancelamento de vaga por contratante ou profissional, com advertência por cancelamento tardio.

Cancelar com menos de `cancelamento_tardio_horas` do início, quando já existe compromisso (vaga
aceita, aguardando pagamento ou contratada), gera uma advertência para quem cancelou. Ao somar
`advertencias_para_suspensao` advertências no período, a conta é suspensa. Um pagamento retido é
estornado por inteiro.
"""

from datetime import timedelta

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.erros import erro_http
from app.models import (
    Advertencia,
    Convite,
    EstadoConvite,
    EstadoPagamento,
    EstadoVaga,
    Usuario,
    Vaga,
)
from app.services import configuracao, notificacoes, pagamentos, sancoes
from app.tempo import agora, como_utc

CANCELAVEIS_PELO_CONTRATANTE = (
    EstadoVaga.PUBLICADA,
    EstadoVaga.AGUARDANDO_ACEITE,
    EstadoVaga.ACEITA,
    EstadoVaga.AGUARDANDO_PAGAMENTO,
    EstadoVaga.CONTRATADA,
)
CANCELAVEIS_PELO_PROFISSIONAL = (
    EstadoVaga.ACEITA,
    EstadoVaga.AGUARDANDO_PAGAMENTO,
    EstadoVaga.CONTRATADA,
)
COM_COMPROMISSO = (
    EstadoVaga.ACEITA,
    EstadoVaga.AGUARDANDO_PAGAMENTO,
    EstadoVaga.CONTRATADA,
)
MOTIVO_ADVERTENCIA = "Cancelamento tardio"


def _papel(db: Session, vaga: Vaga, usuario: Usuario) -> str:
    if (
        usuario.perfil_contratante is not None
        and usuario.perfil_contratante.id == vaga.contratante_id
    ):
        return "contratante"
    if (
        usuario.perfil_profissional is not None
        and vaga.profissional_id == usuario.perfil_profissional.id
    ):
        return "profissional"
    raise erro_http(
        status.HTTP_403_FORBIDDEN,
        "vaga_de_outra_pessoa",
        "Somente quem participa da vaga pode cancelá-la.",
    )


def _advertencias_no_periodo(db: Session, usuario_id: int) -> int:
    dias = int(configuracao.get("periodo_advertencias_dias", db=db))
    return int(
        db.scalar(
            select(func.count(Advertencia.id)).where(
                Advertencia.usuario_id == usuario_id,
                Advertencia.criado_em >= agora() - timedelta(days=dias),
            )
        )
        or 0
    )


def previa(db: Session, vaga: Vaga, usuario: Usuario) -> dict:
    """Informa, antes de confirmar, se o cancelamento é possível e se gera advertência."""
    papel = _papel(db, vaga, usuario)
    permitidos = (
        CANCELAVEIS_PELO_CONTRATANTE if papel == "contratante" else CANCELAVEIS_PELO_PROFISSIONAL
    )
    horas = float(configuracao.get("cancelamento_tardio_horas", db=db))
    faltam = (como_utc(vaga.inicio_em) - agora()).total_seconds() / 3600
    pode = vaga.estado in permitidos and vaga.checkin_em is None
    return {
        "pode_cancelar": pode,
        "gera_advertencia": pode and vaga.estado in COM_COMPROMISSO and faltam < horas,
        "limite_horas": horas,
        "horas_para_inicio": round(faltam, 2),
        "advertencias_no_periodo": _advertencias_no_periodo(db, usuario.id),
        "advertencias_para_suspensao": int(configuracao.get("advertencias_para_suspensao", db=db)),
    }


def cancelar(db: Session, vaga: Vaga, usuario: Usuario, motivo: str) -> dict:
    papel = _papel(db, vaga, usuario)
    permitidos = (
        CANCELAVEIS_PELO_CONTRATANTE if papel == "contratante" else CANCELAVEIS_PELO_PROFISSIONAL
    )
    if vaga.checkin_em is not None:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "servico_iniciado",
            "O profissional já fez o check-in; o serviço não pode ser cancelado.",
        )
    if vaga.estado not in permitidos:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "vaga_nao_cancelavel",
            "A vaga não pode ser cancelada neste estado.",
            estado_atual=vaga.estado.value,
        )

    informacao = previa(db, vaga, usuario)
    tardio = informacao["gera_advertencia"]
    estava_comprometida = vaga.estado in COM_COMPROMISSO

    pagamento = pagamentos.pagamento_da_vaga(db, vaga)
    estornado = False
    if pagamento is not None and pagamento.estado == EstadoPagamento.RETIDO:
        pagamentos.estornar_integral(
            db, pagamento, estado_vaga=EstadoVaga.CANCELADA, commit=False
        )
        estornado = True
    elif pagamento is not None and pagamento.estado == EstadoPagamento.AGUARDANDO:
        pagamento.estado = EstadoPagamento.EXPIRADO

    vaga.estado = EstadoVaga.CANCELADA
    vaga.cancelada_em = agora()
    vaga.cancelada_por_id = usuario.id
    vaga.motivo_cancelamento = motivo.strip()[:255]
    pagamentos._fechar_conversa(db, vaga.id)
    for convite in db.scalars(select(Convite).where(Convite.vaga_id == vaga.id)):
        if convite.estado in (
            EstadoConvite.SUGERIDO,
            EstadoConvite.INTERESSADO,
            EstadoConvite.ESCOLHIDO,
        ):
            convite.estado = EstadoConvite.EXPIRADO

    suspensa_ate = None
    if tardio:
        db.add(Advertencia(usuario_id=usuario.id, vaga_id=vaga.id, motivo=MOTIVO_ADVERTENCIA))
        db.flush()
        limite = int(configuracao.get("advertencias_para_suspensao", db=db))
        if _advertencias_no_periodo(db, usuario.id) >= limite:
            sancao = sancoes.suspender(
                db,
                usuario,
                dias=int(configuracao.get("suspensao_advertencias_dias", db=db)),
                motivo=f"{limite} cancelamentos tardios no período.",
                origem="advertencias",
                commit=False,
            )
            suspensa_ate = como_utc(sancao.fim_em)

    outro_id = _outra_parte(db, vaga, papel)
    if estava_comprometida and outro_id is not None:
        notificacoes.criar_notificacao(
            db,
            usuario_id=outro_id,
            tipo="vaga_cancelada",
            titulo="Vaga cancelada",
            mensagem=f'"{vaga.titulo}" foi cancelada pelo {papel}.',
            vaga_id=vaga.id,
            commit=False,
        )
    db.commit()
    db.refresh(vaga)
    return {
        "estado": vaga.estado.value,
        "advertencia_aplicada": tardio,
        "advertencias_no_periodo": _advertencias_no_periodo(db, usuario.id),
        "valor_estornado": estornado,
        "suspensa_ate": suspensa_ate,
    }


def _outra_parte(db: Session, vaga: Vaga, papel: str) -> int | None:
    contratante_uid, profissional_uid = notificacoes._usuarios_da_vaga(db, vaga)
    return profissional_uid if papel == "contratante" else contratante_uid
