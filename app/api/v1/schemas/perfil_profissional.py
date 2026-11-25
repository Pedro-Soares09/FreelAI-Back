"""Visão pública do perfil do profissional, sem dados sensíveis."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.api.v1.schemas.cadastro import CaracteristicasEntrada
from app.api.v1.schemas.perfil_contratante import AvaliacaoPublica
from app.models.enums import RiscoCategoria


class CategoriaPublica(BaseModel):
    slug: str
    nome: str
    nivel_risco: RiscoCategoria


class PerfilPublicoProfissional(BaseModel):
    """Não expõe CPF, telefone, endereço exato nem arquivos de verificação."""

    usuario_id: int
    nome: str
    foto_url: str | None = None
    bairro: str | None = None
    categorias: list[CategoriaPublica] = Field(default_factory=list)
    caracteristicas: CaracteristicasEntrada
    verificado: bool
    pro: bool
    reputacao: float = Field(description="Nota final de 0 a 5 pela fórmula de reputação.")
    nota_media: float | None = None
    total_avaliacoes: int = 0
    servicos_concluidos: int = 0
    novo: bool = Field(description="Selo Novo enquanto tiver até 5 serviços concluídos.")
    membro_desde: datetime
    avaliacoes: list[AvaliacaoPublica] = Field(default_factory=list)
