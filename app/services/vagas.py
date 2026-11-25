"""Máquina de estados central e regras de ciclo de vida da vaga (US23 / FLX09)."""

from datetime import datetime, timedelta
from enum import StrEnum
import json
import logging
from typing import Any

from fastapi import status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erros import erro_http
from app.models import EstadoVaga, Vaga
from app.services import configuracao
from app.services.varreduras import varredura
from app.tempo import agora, como_utc

logger = logging.getLogger("freelai.vagas.maquina_estados")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)
logger.setLevel(logging.INFO)
logger.propagate = False


class EventoVaga(StrEnum):
    INICIAR_CONTRATACAO = "iniciar_contratacao"
    ACEITAR_CONVITE = "aceitar_convite"
    RECUSAR_CONVITE = "recusar_convite"
    EXPIRAR_CONVITE = "expirar_convite"
    SOLICITAR_PAGAMENTO = "solicitar_pagamento"
    EXPIRAR_PAGAMENTO = "expirar_pagamento"
    CONFIRMAR_CONTRATACAO = "confirmar_contratacao"
    INICIAR_EXECUCAO = "iniciar_execucao"
    ENCERRAR_EXECUCAO = "encerrar_execucao"
    CONCLUIR_SERVICO = "concluir_servico"
    LIBERAR_INTEGRAL = "liberar_integral"
    LIBERAR_PARCIAL = "liberar_parcial"
    ABRIR_DISPUTA = "abrir_disputa"
    REEMBOLSAR = "reembolsar"
    RETORNAR_CONCLUIDA_APOS_DISPUTA = "retornar_concluida_apos_disputa"
    CANCELAR = "cancelar"
    REGISTRAR_FALTA = "registrar_falta"
    EXPIRAR = "expirar"


TRANSICOES_VAGA: dict[tuple[EstadoVaga, EventoVaga], EstadoVaga] = {
    # Publicação e contratação inicial
    (EstadoVaga.PUBLICADA, EventoVaga.INICIAR_CONTRATACAO): EstadoVaga.AGUARDANDO_ACEITE,
    (EstadoVaga.PUBLICADA, EventoVaga.CANCELAR): EstadoVaga.CANCELADA,
    (EstadoVaga.PUBLICADA, EventoVaga.EXPIRAR): EstadoVaga.EXPIRADA,
    # Aguardando aceite do profissional escolhido
    (EstadoVaga.AGUARDANDO_ACEITE, EventoVaga.ACEITAR_CONVITE): EstadoVaga.ACEITA,
    (EstadoVaga.AGUARDANDO_ACEITE, EventoVaga.RECUSAR_CONVITE): EstadoVaga.PUBLICADA,
    (EstadoVaga.AGUARDANDO_ACEITE, EventoVaga.EXPIRAR_CONVITE): EstadoVaga.PUBLICADA,
    (EstadoVaga.AGUARDANDO_ACEITE, EventoVaga.CANCELAR): EstadoVaga.CANCELADA,
    (EstadoVaga.AGUARDANDO_ACEITE, EventoVaga.EXPIRAR): EstadoVaga.EXPIRADA,
    # Aceita -> pagamento / contratação
    (EstadoVaga.ACEITA, EventoVaga.SOLICITAR_PAGAMENTO): EstadoVaga.AGUARDANDO_PAGAMENTO,
    (EstadoVaga.ACEITA, EventoVaga.CONFIRMAR_CONTRATACAO): EstadoVaga.CONTRATADA,
    (EstadoVaga.ACEITA, EventoVaga.CANCELAR): EstadoVaga.CANCELADA,
    # Aguardando pagamento
    (EstadoVaga.AGUARDANDO_PAGAMENTO, EventoVaga.CONFIRMAR_CONTRATACAO): EstadoVaga.CONTRATADA,
    (EstadoVaga.AGUARDANDO_PAGAMENTO, EventoVaga.EXPIRAR_PAGAMENTO): EstadoVaga.PUBLICADA,
    (EstadoVaga.AGUARDANDO_PAGAMENTO, EventoVaga.CANCELAR): EstadoVaga.CANCELADA,
    (EstadoVaga.AGUARDANDO_PAGAMENTO, EventoVaga.EXPIRAR): EstadoVaga.EXPIRADA,
    # Contratada -> execução / encerramento / conclusão / falta / cancelamento / disputa
    (EstadoVaga.CONTRATADA, EventoVaga.INICIAR_EXECUCAO): EstadoVaga.EM_EXECUCAO,
    (EstadoVaga.CONTRATADA, EventoVaga.ENCERRAR_EXECUCAO): EstadoVaga.ENCERRADA,
    (EstadoVaga.CONTRATADA, EventoVaga.CONCLUIR_SERVICO): EstadoVaga.CONCLUIDA,
    (EstadoVaga.CONTRATADA, EventoVaga.REGISTRAR_FALTA): EstadoVaga.FALTA,
    (EstadoVaga.CONTRATADA, EventoVaga.CANCELAR): EstadoVaga.CANCELADA,
    (EstadoVaga.CONTRATADA, EventoVaga.ABRIR_DISPUTA): EstadoVaga.EM_DISPUTA,
    # Em execução
    (EstadoVaga.EM_EXECUCAO, EventoVaga.ENCERRAR_EXECUCAO): EstadoVaga.ENCERRADA,
    (EstadoVaga.EM_EXECUCAO, EventoVaga.CONCLUIR_SERVICO): EstadoVaga.CONCLUIDA,
    (EstadoVaga.EM_EXECUCAO, EventoVaga.REGISTRAR_FALTA): EstadoVaga.FALTA,
    (EstadoVaga.EM_EXECUCAO, EventoVaga.CANCELAR): EstadoVaga.CANCELADA,
    (EstadoVaga.EM_EXECUCAO, EventoVaga.ABRIR_DISPUTA): EstadoVaga.EM_DISPUTA,
    # Encerrada (aguardando confirmação / conclusão)
    (EstadoVaga.ENCERRADA, EventoVaga.CONCLUIR_SERVICO): EstadoVaga.CONCLUIDA,
    (EstadoVaga.ENCERRADA, EventoVaga.ABRIR_DISPUTA): EstadoVaga.EM_DISPUTA,
    (EstadoVaga.ENCERRADA, EventoVaga.CANCELAR): EstadoVaga.CANCELADA,
    (EstadoVaga.CONTRATADA, EventoVaga.LIBERAR_INTEGRAL): EstadoVaga.LIBERADA,
    (EstadoVaga.CONTRATADA, EventoVaga.LIBERAR_PARCIAL): EstadoVaga.LIBERADA_PARCIAL,
    (EstadoVaga.CONTRATADA, EventoVaga.REEMBOLSAR): EstadoVaga.REEMBOLSADA,
    (EstadoVaga.EM_EXECUCAO, EventoVaga.LIBERAR_INTEGRAL): EstadoVaga.LIBERADA,
    (EstadoVaga.EM_EXECUCAO, EventoVaga.LIBERAR_PARCIAL): EstadoVaga.LIBERADA_PARCIAL,
    (EstadoVaga.EM_EXECUCAO, EventoVaga.REEMBOLSAR): EstadoVaga.REEMBOLSADA,
    (EstadoVaga.ENCERRADA, EventoVaga.LIBERAR_INTEGRAL): EstadoVaga.LIBERADA,
    (EstadoVaga.ENCERRADA, EventoVaga.LIBERAR_PARCIAL): EstadoVaga.LIBERADA_PARCIAL,
    (EstadoVaga.ENCERRADA, EventoVaga.REEMBOLSAR): EstadoVaga.REEMBOLSADA,
    # Concluída -> liberação / disputa / reembolso
    (EstadoVaga.CONCLUIDA, EventoVaga.LIBERAR_INTEGRAL): EstadoVaga.LIBERADA,
    (EstadoVaga.CONCLUIDA, EventoVaga.LIBERAR_PARCIAL): EstadoVaga.LIBERADA_PARCIAL,
    (EstadoVaga.CONCLUIDA, EventoVaga.ABRIR_DISPUTA): EstadoVaga.EM_DISPUTA,
    (EstadoVaga.CONCLUIDA, EventoVaga.REEMBOLSAR): EstadoVaga.REEMBOLSADA,
    # Em disputa -> resolução
    (EstadoVaga.EM_DISPUTA, EventoVaga.LIBERAR_INTEGRAL): EstadoVaga.LIBERADA,
    (EstadoVaga.EM_DISPUTA, EventoVaga.LIBERAR_PARCIAL): EstadoVaga.LIBERADA_PARCIAL,
    (EstadoVaga.EM_DISPUTA, EventoVaga.REEMBOLSAR): EstadoVaga.REEMBOLSADA,
    (EstadoVaga.EM_DISPUTA, EventoVaga.RETORNAR_CONCLUIDA_APOS_DISPUTA): EstadoVaga.CONCLUIDA,
}


def calcular_prazo_publicacao(db: Session, inicio_em: datetime, referencia: datetime | None = None) -> datetime:
    """Calcula o vencimento efetivo de uma vaga publicada: o que vier primeiro entre prazo/12h e inicio_em."""
    instante = como_utc(referencia or agora())
    minutos = configuracao.get("expiracao_vaga_min", db=db)
    limite_publicacao = instante + timedelta(minutes=minutos)
    inicio_utc = como_utc(inicio_em)
    return min(limite_publicacao, inicio_utc)


def calcular_vencimento_vaga_publicada(
    db: Session, vaga: Vaga, referencia: datetime | None = None
) -> datetime:
    """Retorna o instante de expiração efetivo para uma vaga em PUBLICADA (prazo_em ou início_em, o que vier primeiro)."""
    inicio_utc = como_utc(vaga.inicio_em)
    if vaga.prazo_em is not None:
        return min(como_utc(vaga.prazo_em), inicio_utc)
    minutos = configuracao.get("expiracao_vaga_min", db=db)
    base = como_utc(vaga.criado_em) if getattr(vaga, "criado_em", None) is not None else como_utc(referencia or agora())
    return min(base + timedelta(minutes=minutos), inicio_utc)


def _registrar_log_transicao(
    vaga: Vaga,
    estado_anterior: EstadoVaga,
    evento_nome: str,
    estado_novo: EstadoVaga,
    motivo: str | None = None,
    extras: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "evento_auditoria": "vaga_transicao_estado",
        "vaga_id": getattr(vaga, "id", None),
        "estado_anterior": estado_anterior.value,
        "evento": evento_nome,
        "estado_novo": estado_novo.value,
        "ocorrido_em": agora().isoformat(),
    }
    if motivo:
        payload["motivo"] = motivo
    if extras:
        payload.update(extras)
    logger.info(json.dumps(payload, ensure_ascii=False))


def transicionar(
    vaga: Vaga,
    evento: EventoVaga | str,
    *,
    motivo: str | None = None,
    extras: dict[str, Any] | None = None,
) -> Vaga:
    """Valida e executa uma transição de estado da vaga com registro de auditoria.

    Em caso de transição inválida, devolve HTTP 409 contendo `estado_atual`.
    """
    estado_atual = vaga.estado if isinstance(vaga.estado, EstadoVaga) else EstadoVaga(vaga.estado)
    try:
        evento_enum = evento if isinstance(evento, EventoVaga) else EventoVaga(str(evento))
    except ValueError:
        evento_nome = str(evento)
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "transicao_estado_invalida",
            f"Evento '{evento_nome}' inválido para a vaga no estado '{estado_atual.value}'.",
            estado_atual=estado_atual.value,
            evento=evento_nome,
        ) from None

    destino = TRANSICOES_VAGA.get((estado_atual, evento_enum))
    if destino is None:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "transicao_estado_invalida",
            f"Transição inválida a partir do estado '{estado_atual.value}' com o evento '{evento_enum.value}'.",
            estado_atual=estado_atual.value,
            evento=evento_enum.value,
        )

    vaga.estado = destino
    _registrar_log_transicao(
        vaga=vaga,
        estado_anterior=estado_atual,
        evento_nome=evento_enum.value,
        estado_novo=destino,
        motivo=motivo,
        extras=extras,
    )
    return vaga


@varredura("expiracao_vagas_publicadas")
def expirar_vagas_publicadas(db: Session) -> int:
    """Expira vagas em `publicada` quando `prazo_em` (ou 12h/perfil demo) ou `inicio_em` vencer (o que vier primeiro)."""
    instante = agora()
    candidatas = db.scalars(
        select(Vaga).where(Vaga.estado == EstadoVaga.PUBLICADA)
    ).all()

    expiradas = 0
    for vaga in candidatas:
        vencimento = calcular_vencimento_vaga_publicada(db, vaga, referencia=instante)
        if vencimento <= instante:
            motivo = (
                "horario_inicio_atingido"
                if como_utc(vaga.inicio_em) <= vencimento
                else "prazo_publicacao_expirado"
            )
            transicionar(
                vaga,
                EventoVaga.EXPIRAR,
                motivo=motivo,
                extras={"vencimento_em": vencimento.isoformat()},
            )
            vaga.prazo_em = None
            expiradas += 1

    if expiradas:
        db.commit()
    return expiradas
