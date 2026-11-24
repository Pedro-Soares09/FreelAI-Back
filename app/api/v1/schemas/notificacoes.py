"""Schemas da central de notificações no app (polling REST, atraso por plano e leitura) — US62."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class EventoNotificacao(StrEnum):
    """Tipos de evento que disparam notificação na central do aplicativo (regra PAG05)."""

    SELECIONADO_TOP3 = "selecionado_top3"
    CONVITE_ESCOLHIDO = "convite_escolhido"
    CONVITE_ACEITO = "convite_aceito"
    CONTRATO_PRONTO = "contrato_pronto"
    PAGAMENTO_RETIDO = "pagamento_retido"
    LEMBRETE_CONFIRMACAO = "lembrete_confirmacao"
    PAGAMENTO_LIBERADO = "pagamento_liberado"
    AVALIACAO_PENDENTE = "avaliacao_pendente"
    DENUNCIA_DECIDIDA = "denuncia_decidida"


class NotificacaoDetalhe(BaseModel):
    """Notificação já visível ao usuário na central do aplicativo."""

    id: int
    usuario_id: int
    tipo: str
    titulo: str
    mensagem: str | None = None
    vaga_id: int | None = None
    visivel_em: datetime
    lida_em: datetime | None = None
    lida: bool = Field(description="Verdadeiro quando `lida_em` está preenchido.")
    criado_em: datetime

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": 10,
                    "usuario_id": 7,
                    "tipo": "selecionado_top3",
                    "titulo": "Você foi selecionado para uma vaga",
                    "mensagem": "Sua conta está entre os 3 profissionais sugeridos para a vaga.",
                    "vaga_id": 42,
                    "visivel_em": "2026-11-20T14:02:00Z",
                    "lida_em": None,
                    "lida": False,
                    "criado_em": "2026-11-20T14:00:00Z",
                }
            ]
        },
    )


class CentralNotificacoes(BaseModel):
    """Resposta do polling REST da central de notificações: lista visível e total de não lidas."""

    nao_lidas: int = Field(description="Total de notificações já visíveis e ainda não lidas.")
    itens: list[NotificacaoDetalhe]

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "nao_lidas": 1,
                    "itens": NotificacaoDetalhe.model_config["json_schema_extra"]["examples"],
                }
            ]
        }
    )


class ContagemNaoLidas(BaseModel):
    """Quantidade de notificações visíveis ainda não lidas pelo usuário."""

    nao_lidas: int

    model_config = ConfigDict(json_schema_extra={"examples": [{"nao_lidas": 2}]})


class ResultadoLeituraTodas(BaseModel):
    """Quantidade de notificações visíveis marcadas como lidas em lote."""

    marcadas: int
    lida_em: datetime | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "marcadas": 3,
                    "lida_em": "2026-11-20T14:10:00Z",
                }
            ]
        }
    )
