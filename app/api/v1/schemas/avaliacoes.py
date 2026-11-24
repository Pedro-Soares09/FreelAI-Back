"""Schemas das rotas de avaliação mútua, reputação 70/20/10 e selo Novo (US55)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AvaliacaoEntrada(BaseModel):
    """Nota de 1 a 5 e comentário opcional enviados por um participante após a conclusão."""

    nota: int = Field(
        ge=1, le=5, description="Nota de 1 a 5 atribuída à outra parte.", examples=[5]
    )
    comentario: str | None = Field(
        default=None,
        max_length=1000,
        description="Comentário sobre o serviço prestado ou contratado.",
        examples=["Pontual e cuidadoso."],
    )

    @field_validator("comentario")
    @classmethod
    def _aparar_comentario(cls, valor: str | None) -> str | None:
        if valor is None:
            return None
        limpo = valor.strip()
        return limpo or None


class AvaliacaoDetalhe(BaseModel):
    """Avaliação registrada. Só fica visível quando ambos avaliam ou o prazo de 48h encerra."""

    id: int
    vaga_id: int
    avaliador_id: int
    avaliado_id: int
    nota: int
    comentario: str | None = None
    criado_em: datetime
    visivel_em: datetime | None = None
    visivel: bool = Field(
        description="Verdadeiro quando ambos já avaliaram ou o prazo de 48h encerrou."
    )

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": 15,
                    "vaga_id": 42,
                    "avaliador_id": 3,
                    "avaliado_id": 7,
                    "nota": 5,
                    "comentario": "Pontual e cuidadoso.",
                    "criado_em": "2026-11-20T18:30:00Z",
                    "visivel_em": "2026-11-20T19:00:00Z",
                    "visivel": True,
                }
            ]
        },
    )


class AvaliacoesDaVaga(BaseModel):
    """Estado das avaliações de uma vaga para quem consulta."""

    vaga_id: int
    prazo_em: datetime = Field(description="Fim da janela de 48h para avaliar.")
    reveladas: bool = Field(
        description="Verdadeiro quando ambos já avaliaram ou o prazo de 48h encerrou."
    )
    minha_avaliacao: AvaliacaoDetalhe | None = Field(
        default=None,
        description="Avaliação feita pelo próprio usuário que consulta (quando autenticada).",
    )
    avaliacao_recebida: AvaliacaoDetalhe | None = Field(
        default=None,
        description="Avaliação da outra parte; permanece nula enquanto ainda estiver oculta.",
    )
    avaliacoes: list[AvaliacaoDetalhe] = Field(
        description="Avaliações já visíveis publicamente desta vaga."
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "vaga_id": 42,
                    "prazo_em": "2026-11-22T17:00:00Z",
                    "reveladas": True,
                    "minha_avaliacao": {
                        "id": 15,
                        "vaga_id": 42,
                        "avaliador_id": 3,
                        "avaliado_id": 7,
                        "nota": 5,
                        "comentario": "Pontual e cuidadoso.",
                        "criado_em": "2026-11-20T18:30:00Z",
                        "visivel_em": "2026-11-20T19:00:00Z",
                        "visivel": True,
                    },
                    "avaliacao_recebida": {
                        "id": 16,
                        "vaga_id": 42,
                        "avaliador_id": 7,
                        "avaliado_id": 3,
                        "nota": 5,
                        "comentario": "Contratante claro e organizado.",
                        "criado_em": "2026-11-20T19:00:00Z",
                        "visivel_em": "2026-11-20T19:00:00Z",
                        "visivel": True,
                    },
                    "avaliacoes": [],
                }
            ]
        }
    )


class ReputacaoDetalhe(BaseModel):
    """Reputação 70/20/10 de um usuário, detalhando cada componente e o selo Novo."""

    usuario_id: int
    reputacao: float = Field(description="Nota final de 0 a 5 pela fórmula 70/20/10.")
    nota_media: float | None = Field(
        default=None,
        description="Média real das avaliações visíveis, ou nulo quando ainda não há avaliações.",
    )
    media_considerada: float = Field(
        description="Média usada na fórmula (4,0 neutra quando não há avaliações visíveis)."
    )
    total_avaliacoes: int = Field(description="Quantidade de avaliações visíveis recebidas.")
    nota_cancelamentos: float = Field(
        description="Parcela de cancelamentos (0 a 5): 5 * (1 - taxa_cancelamento_tardio)."
    )
    taxa_cancelamento_tardio: float = Field(
        description="Proporção (0 a 1) de cancelamentos tardios nos últimos 20 compromissos."
    )
    cancelamentos_tardios_ultimos_20: int
    amostra_compromissos: int = Field(
        description="Quantidade de compromissos considerados na janela (máximo 20)."
    )
    nota_denuncias: float = Field(
        description="Parcela de denúncias (0 a 5): max(0, 5 - 2 * denuncias_procedentes)."
    )
    denuncias_procedentes: int
    servicos_concluidos: int = Field(
        description="Serviços concluídos como profissional (0 quando não possui perfil)."
    )
    novo: bool = Field(
        description="Exibe o selo Novo enquanto tiver até 5 serviços concluídos; cai no 6º."
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "usuario_id": 7,
                    "reputacao": 4.4,
                    "nota_media": 4.5,
                    "media_considerada": 4.5,
                    "total_avaliacoes": 4,
                    "nota_cancelamentos": 4.75,
                    "taxa_cancelamento_tardio": 0.05,
                    "cancelamentos_tardios_ultimos_20": 1,
                    "amostra_compromissos": 20,
                    "nota_denuncias": 3.0,
                    "denuncias_procedentes": 1,
                    "servicos_concluidos": 4,
                    "novo": True,
                }
            ]
        }
    )
