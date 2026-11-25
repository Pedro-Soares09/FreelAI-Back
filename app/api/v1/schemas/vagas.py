"""Schemas das rotas de vagas, convites e top 3.

Os nomes e formatos seguem o que o front consome: identificadores como texto, valores em reais
como número e estados em vocabulário reduzido (ver `STATUS_VAGA` e `STATUS_CONVITE`).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

NivelRisco = Literal["baixo", "medio", "alto"]


class ContratanteResumo(BaseModel):
    id: str
    usuario_id: int | None = None
    nome: str
    foto_url: str | None = None
    verificado: bool
    nota_media: float
    total_avaliacoes: int
    pro: bool = False
    adicionais_pagos: int = 0
    destaque: bool = False
    cor_destaque: str | None = None


class ConviteVaga(BaseModel):
    id: str
    vaga_id: str
    profissional_id: str
    status: str
    valor_proposto: float | None = None
    prazo_em: datetime | None = None
    rodada: int


class VagaDetalhe(BaseModel):
    id: str
    titulo: str
    descricao: str
    categoria: str
    nivel_risco: NivelRisco
    valor: float
    duracao_horas: float
    data_inicio: datetime
    bairro: str
    distancia_km: float
    latitude: float
    longitude: float
    endereco_completo: str | None = None
    status: str
    contratante: ContratanteResumo
    meu_convite: ConviteVaga | None = None


class RespostaChecklist(BaseModel):
    item_id: int
    atendido: bool


class VagaCriar(BaseModel):
    titulo: str = Field(min_length=3, max_length=120)
    descricao: str = Field(min_length=5)
    categoria: str = Field(description="Slug da categoria, obtido em GET /categorias.")
    valor: float = Field(gt=0, le=100000)
    duracao_horas: float = Field(gt=0, le=24)
    data_inicio: datetime
    bairro: str = Field(min_length=2, max_length=80)
    logradouro: str | None = Field(default=None, max_length=160)
    latitude: float | None = Field(
        default=None, ge=-90, le=90, description="Opcional: deduzida do bairro quando ausente."
    )
    longitude: float | None = Field(default=None, ge=-180, le=180)
    checklist: list[RespostaChecklist] = Field(
        default_factory=list,
        description="Respostas do checklist de segurança; obrigatório nas categorias de risco "
        "médio ou alto (itens em GET /categorias/{slug}/seguranca).",
    )


class CategoriaResumo(BaseModel):
    slug: str
    nome: str
    nivel_risco: NivelRisco


class InteresseEntrada(BaseModel):
    valor_proposto: float | None = Field(default=None, gt=0)


class ConviteComVaga(BaseModel):
    convite: ConviteVaga
    vaga: VagaDetalhe


class AceiteResposta(BaseModel):
    vaga_id: str
    conversa_id: str


class ProfissionalRecomendado(BaseModel):
    id: str
    nome: str
    foto_url: str | None = None
    verificado: bool
    nota_media: float
    total_avaliacoes: int
    distancia_km: float
    novo: bool
    pro: bool = False
    demonstrou_interesse: bool
    valor_proposto: float | None = None
    resumo: str
    habilidades: list[str]
    estado: Literal["disponivel", "falhou"]


class ConviteContratante(BaseModel):
    id: str
    profissional_id: str
    status: Literal["em_andamento", "aceito"]
    prazo_em: datetime


class Top3Contratacao(BaseModel):
    vaga: VagaDetalhe
    rodada: int
    profissionais: list[ProfissionalRecomendado]
    convite_atual: ConviteContratante | None = None


class EscolhaEntrada(BaseModel):
    profissional_id: str
