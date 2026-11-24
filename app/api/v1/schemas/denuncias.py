"""Schemas de entrada e saída para denúncias (chat e perfil) e fila da administração (US57)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import EstadoConta, EstadoDenuncia, OrigemDenuncia


class DenunciaEntrada(BaseModel):
    """Dados enviados por um usuário para denunciar outro (a partir do perfil, vaga ou chat)."""

    denunciado_id: int | None = Field(
        default=None,
        description=(
            "ID do usuário denunciado. Opcional quando `conversa_id` ou `vaga_id` for informado "
            "(inferido automaticamente como a outra parte)."
        ),
        examples=[7],
    )
    motivo: str = Field(
        min_length=1,
        max_length=80,
        description="Motivo principal da denúncia.",
        examples=["comportamento_inadequado"],
    )
    descricao: str | None = Field(
        default=None,
        max_length=2000,
        description="Relato detalhado do ocorrido.",
        examples=["Solicitou pagamento por fora da plataforma durante a conversa."],
    )
    vaga_id: int | None = Field(
        default=None,
        description="ID da vaga relacionada à denúncia, quando houver.",
        examples=[42],
    )
    conversa_id: int | None = Field(
        default=None,
        description="ID da conversa do chat relacionada à denúncia, quando houver.",
        examples=[9],
    )

    @field_validator("motivo")
    @classmethod
    def _motivo_nao_vazio(cls, valor: str) -> str:
        limpo = valor.strip()
        if not limpo:
            raise ValueError("O motivo da denúncia não pode ficar em branco.")
        return limpo

    @field_validator("descricao")
    @classmethod
    def _aparar_descricao(cls, valor: str | None) -> str | None:
        if valor is None:
            return None
        limpo = valor.strip()
        return limpo or None


class DenunciaPublicaResumo(BaseModel):
    """Visão da denúncia para usuários comuns. Nunca expõe `denunciante_id` ao denunciado."""

    id: int
    denunciado_id: int
    vaga_id: int | None = None
    conversa_id: int | None = None
    motivo: str
    descricao: str | None = None
    estado: EstadoDenuncia
    decisao_motivo: str | None = None
    criado_em: datetime
    decidido_em: datetime | None = None

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": 1,
                    "denunciado_id": 7,
                    "vaga_id": 42,
                    "conversa_id": 9,
                    "motivo": "comportamento_inadequado",
                    "descricao": "Solicitou pagamento por fora da plataforma.",
                    "estado": "aberta",
                    "decisao_motivo": None,
                    "criado_em": "2026-11-20T14:00:00Z",
                    "decidido_em": None,
                }
            ]
        },
    )


class DenunciaFilaAdmin(BaseModel):
    """Item da fila de denúncias na administração, com tempo em espera e meta de 48h úteis."""

    id: int
    denunciante_id: int | None = Field(
        default=None,
        description="Autor da denúncia (nulo quando gerada automaticamente pelo chat).",
    )
    denunciado_id: int
    vaga_id: int | None = None
    conversa_id: int | None = None
    origem: OrigemDenuncia
    motivo: str
    descricao: str | None = None
    estado: EstadoDenuncia
    decisao_motivo: str | None = None
    criado_em: datetime
    decidido_em: datetime | None = None
    tempo_espera_minutos: int = Field(
        description="Tempo total em espera (em minutos) desde a abertura até a decisão ou agora."
    )
    tempo_espera_horas_uteis: float = Field(
        description="Horas úteis acumuladas em espera (segunda a sexta-feira)."
    )
    meta_horas_uteis: int = Field(
        default=48,
        description="Meta de atendimento da fila em horas úteis (48h úteis).",
    )
    acima_da_meta: bool = Field(
        description="Verdadeiro quando o tempo em horas úteis ultrapassou a meta de 48h úteis."
    )
    denuncias_procedentes_acumuladas: int = Field(
        default=0,
        description="Total de denúncias procedentes acumuladas pelo denunciado.",
    )
    denunciado_estado: EstadoConta | None = Field(
        default=None, description="Situação atual da conta denunciada (banida após 2 penalidades)."
    )
    conversa_bloqueada: bool = Field(
        default=False,
        description="Denúncia procedente: a troca de mensagens entre as partes está bloqueada.",
    )

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": 1,
                    "denunciante_id": 3,
                    "denunciado_id": 7,
                    "vaga_id": 42,
                    "conversa_id": 9,
                    "origem": "usuario",
                    "motivo": "comportamento_inadequado",
                    "descricao": "Solicitou pagamento por fora da plataforma.",
                    "estado": "aberta",
                    "decisao_motivo": None,
                    "criado_em": "2026-11-20T14:00:00Z",
                    "decidido_em": None,
                    "tempo_espera_minutos": 120,
                    "tempo_espera_horas_uteis": 2.0,
                    "meta_horas_uteis": 48,
                    "acima_da_meta": False,
                    "denuncias_procedentes_acumuladas": 0,
                }
            ]
        },
    )


class DecisaoDenunciaEntrada(BaseModel):
    """Decisão administrativa sobre uma denúncia aberta (procedente ou improcedente + motivo)."""

    decisao: Literal["procedente", "improcedente"] = Field(
        description="Resultado da análise da denúncia.",
        examples=["procedente"],
    )
    motivo: str = Field(
        min_length=1,
        max_length=255,
        description="Justificativa obrigatória da decisão administrativa.",
        examples=["Confirmada tentativa de negociação fora da plataforma no histórico."],
    )

    @field_validator("motivo")
    @classmethod
    def _motivo_decisao_nao_vazio(cls, valor: str) -> str:
        limpo = valor.strip()
        if not limpo:
            raise ValueError("O motivo da decisão não pode ficar em branco.")
        return limpo


class UsuarioBanido(BaseModel):
    """Conta banida de forma permanente por reincidência em denúncias procedentes."""

    usuario_id: int
    nome: str
    email: str
    penalidades: int
