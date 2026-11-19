"""Schemas para abertura de disputa (contestação em 24h) e decisão na aba do admin (US61)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.v1.schemas.pagamentos import PagamentoDetalhe
from app.models import EstadoDisputa, EstadoVaga


class DisputaEntrada(BaseModel):
    """Relato enviado pelo contratante para contestar o serviço dentro das 24h de confirmação."""

    descricao: str = Field(
        min_length=1,
        max_length=2000,
        description="Motivo detalhado da contestação do serviço.",
        examples=["O serviço foi interrompido pela metade e parte do móvel ficou desmontada."],
    )

    @field_validator("descricao")
    @classmethod
    def _descricao_nao_vazia(cls, valor: str) -> str:
        limpo = valor.strip()
        if not limpo:
            raise ValueError("A descrição da disputa não pode ficar em branco.")
        return limpo


class DisputaDetalhe(BaseModel):
    """Estado da disputa de uma vaga e o pagamento associado."""

    id: int
    vaga_id: int
    vaga_estado: EstadoVaga
    aberta_por_id: int
    descricao: str
    estado: EstadoDisputa
    percentual_reembolso: int | None = None
    decisao_motivo: str | None = None
    criado_em: datetime
    decidido_em: datetime | None = None
    pagamento: PagamentoDetalhe

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": 3,
                    "vaga_id": 42,
                    "vaga_estado": "em_disputa",
                    "aberta_por_id": 3,
                    "descricao": "Serviço interrompido pela metade.",
                    "estado": "aberta",
                    "percentual_reembolso": None,
                    "decisao_motivo": None,
                    "criado_em": "2026-11-20T18:00:00Z",
                    "decidido_em": None,
                    "pagamento": PagamentoDetalhe.model_config["json_schema_extra"]["examples"][0],
                }
            ]
        },
    )


class MensagemDisputaAdmin(BaseModel):
    """Mensagem do histórico do chat da vaga exibida na aba de disputas do admin."""

    id: int
    conversa_id: int
    autor_id: int
    texto: str | None = None
    anexo_url: str | None = None
    enviada_em: datetime
    lida_em: datetime | None = None


class DisputaAdminDetalhe(BaseModel):
    """Visão completa da aba de disputas no admin: disputa, pagamento, chat e anexos da vaga."""

    id: int
    vaga_id: int
    vaga_titulo: str
    vaga_estado: EstadoVaga
    contratante_usuario_id: int
    profissional_usuario_id: int | None = None
    aberta_por_id: int
    descricao: str
    estado: EstadoDisputa
    percentual_reembolso: int | None = None
    decisao_motivo: str | None = None
    criado_em: datetime
    decidido_em: datetime | None = None
    tempo_espera_minutos: int = Field(
        description="Tempo em espera (em minutos) desde a abertura até a decisão ou agora."
    )
    tempo_espera_horas_uteis: float = Field(
        description="Horas úteis acumuladas em espera (meta de 48h úteis)."
    )
    meta_horas_uteis: int = 48
    acima_da_meta: bool = Field(
        description="Verdadeiro quando o tempo em horas úteis ultrapassou a meta de 48h úteis."
    )
    conversa_id: int | None = None
    chat: list[MensagemDisputaAdmin] = Field(
        description="Histórico completo de mensagens da conversa da vaga."
    )
    anexos: list[str] = Field(
        description="Lista de URLs de anexos enviados na conversa da vaga."
    )
    pagamento: PagamentoDetalhe

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": 3,
                    "vaga_id": 42,
                    "vaga_titulo": "Montagem de guarda-roupa",
                    "vaga_estado": "em_disputa",
                    "contratante_usuario_id": 3,
                    "profissional_usuario_id": 7,
                    "aberta_por_id": 3,
                    "descricao": "Serviço interrompido pela metade.",
                    "estado": "aberta",
                    "percentual_reembolso": None,
                    "decisao_motivo": None,
                    "criado_em": "2026-11-20T18:00:00Z",
                    "decidido_em": None,
                    "tempo_espera_minutos": 60,
                    "tempo_espera_horas_uteis": 1.0,
                    "meta_horas_uteis": 48,
                    "acima_da_meta": False,
                    "conversa_id": 9,
                    "chat": [
                        {
                            "id": 1,
                            "conversa_id": 9,
                            "autor_id": 3,
                            "texto": "Segue foto de como ficou.",
                            "anexo_url": "/api/v1/arquivos/anexo/foto1.jpg",
                            "enviada_em": "2026-11-20T17:50:00Z",
                            "lida_em": None,
                        }
                    ],
                    "anexos": ["/api/v1/arquivos/anexo/foto1.jpg"],
                    "pagamento": PagamentoDetalhe.model_config["json_schema_extra"]["examples"][0],
                }
            ]
        },
    )


class DecisaoDisputaEntrada(BaseModel):
    """Decisão administrativa sobre uma disputa aberta (reembolso total, parcial ou integral)."""

    decisao: Literal["reembolso_total", "reembolso_parcial", "pagamento_integral"] = Field(
        description=(
            "Resultado da disputa: `reembolso_total` (estorna 100%), "
            "`reembolso_parcial` (libera parcela ao profissional e estorna o resto) "
            "ou `pagamento_integral` (libera 100% ao profissional)."
        ),
        examples=["reembolso_parcial"],
    )
    motivo: str = Field(
        min_length=1,
        max_length=255,
        description="Justificativa obrigatória da decisão administrativa.",
        examples=["Metade do serviço foi comprovadamente executada pelas fotos do chat."],
    )
    percentual_reembolso: int | None = Field(
        default=None,
        ge=1,
        le=99,
        description=(
            "Percentual (de 1 a 99) estornado ao contratante quando `decisao = reembolso_parcial`. "
            "O restante `(100 - percentual_reembolso)%` é liberado ao profissional."
        ),
        examples=[40],
    )
    percentual_profissional: int | None = Field(
        default=None,
        ge=1,
        le=99,
        description=(
            "Alternativa a `percentual_reembolso`: percentual (de 1 a 99) liberado ao "
            "profissional em `reembolso_parcial` (o resto é estornado)."
        ),
        examples=[60],
    )

    @field_validator("motivo")
    @classmethod
    def _motivo_nao_vazio(cls, valor: str) -> str:
        limpo = valor.strip()
        if not limpo:
            raise ValueError("O motivo da decisão não pode ficar em branco.")
        return limpo

    @model_validator(mode="after")
    def _validar_percentual_parcial(self) -> "DecisaoDisputaEntrada":
        if self.decisao == "reembolso_parcial":
            if self.percentual_reembolso is None and self.percentual_profissional is None:
                raise ValueError(
                    "Informe `percentual_reembolso` (ou `percentual_profissional`) "
                    "para reembolso parcial."
                )
            if (
                self.percentual_reembolso is not None
                and self.percentual_profissional is not None
                and self.percentual_reembolso + self.percentual_profissional != 100
            ):
                raise ValueError(
                    "A soma de `percentual_reembolso` e `percentual_profissional` deve ser 100."
                )
            if self.percentual_reembolso is None and self.percentual_profissional is not None:
                self.percentual_reembolso = 100 - self.percentual_profissional
            if self.percentual_profissional is None and self.percentual_reembolso is not None:
                self.percentual_profissional = 100 - self.percentual_reembolso
        else:
            if self.percentual_reembolso is not None or self.percentual_profissional is not None:
                raise ValueError(
                    "Percentual parcial só deve ser informado quando `decisao = reembolso_parcial`."
                )
        return self
