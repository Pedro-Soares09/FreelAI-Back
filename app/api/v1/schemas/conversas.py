"""Schemas das rotas de conversas e mensagens do chat (FLX07)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MensagemEntrada(BaseModel):
    """Conteúdo enviado pelo participante no chat (texto, anexo ou ambos)."""

    texto: str | None = Field(
        default=None,
        max_length=4000,
        description="Texto da mensagem.",
        examples=["Chego às 14h."],
    )
    anexo_url: str | None = Field(
        default=None,
        max_length=255,
        description="URL de anexo enviado em `/api/v1/conversas/{conversa_id}/anexos`.",
        examples=["/api/v1/conversas/1/anexos/a1b2c3d4e5f60718293a4b5c6d7e8f90.png"],
    )

    @model_validator(mode="after")
    def _exigir_texto_ou_anexo(self) -> "MensagemEntrada":
        texto = self.texto.strip() if self.texto is not None else None
        anexo = self.anexo_url.strip() if self.anexo_url is not None else None
        self.texto = texto or None
        self.anexo_url = anexo or None
        if self.texto is None and self.anexo_url is None:
            raise ValueError("A mensagem precisa ter texto ou anexo.")
        return self


class MensagemDetalhe(BaseModel):
    """Mensagem persistida no histórico de uma conversa."""

    id: int
    conversa_id: int
    autor_id: int
    texto: str | None = None
    anexo_url: str | None = None
    enviada_em: datetime
    lida_em: datetime | None = None

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": 101,
                    "conversa_id": 9,
                    "autor_id": 7,
                    "texto": "Chego às 14h.",
                    "anexo_url": None,
                    "enviada_em": "2026-11-19T18:30:00Z",
                    "lida_em": None,
                }
            ]
        },
    )


class ResultadoVisto(BaseModel):
    """Quantidade de mensagens recebidas marcadas como vistas."""

    conversa_id: int
    marcadas: int
    lida_em: datetime | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "conversa_id": 9,
                    "marcadas": 2,
                    "lida_em": "2026-11-19T18:35:00Z",
                }
            ]
        }
    )
