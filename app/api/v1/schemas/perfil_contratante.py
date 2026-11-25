"""Personalização do perfil do contratante e visão pública do perfil."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.api.v1.schemas.cadastro import DadosContratanteEntrada
from app.models.enums import TipoPessoa

# Paleta fixa de cores de destaque. O front mapeia cada nome para um tom com contraste adequado.
CoresDestaque = Literal["lilas", "azul", "verde", "rosa", "laranja", "grafite"]


class AtualizacaoContratanteEntrada(DadosContratanteEntrada):
    """Dados cadastrais e personalização do perfil; só o que for enviado é alterado."""

    bio: str | None = Field(default=None, max_length=300)
    bairro: str | None = Field(default=None, max_length=80)
    horario_contato: str | None = Field(default=None, max_length=80)
    categorias_interesse: list[str] | None = Field(default=None, max_length=10)
    exigir_verificado: bool | None = None
    aceita_notificacoes: bool | None = None
    cor_destaque: CoresDestaque | None = None
    avatar_arquivo_id: int | None = Field(
        default=None, description="Arquivo do tipo foto já enviado; 0 remove o avatar."
    )

    @field_validator("bio", "bairro", "horario_contato")
    @classmethod
    def _limpar(cls, valor: str | None) -> str | None:
        if valor is None:
            return None
        valor = valor.strip()
        return valor or None


class CategoriaDeInteresse(BaseModel):
    slug: str
    nome: str


class AvaliacaoPublica(BaseModel):
    nota: int
    comentario: str | None = None
    avaliador_nome: str
    vaga_titulo: str
    criado_em: datetime


class PerfilPublicoContratante(BaseModel):
    """Visão pública do contratante, sem documento, telefone, selfie nem e-mail."""

    usuario_id: int
    nome: str
    tipo_pessoa: TipoPessoa
    bio: str | None = None
    bairro: str | None = None
    horario_contato: str | None = None
    categorias_interesse: list[CategoriaDeInteresse] = Field(default_factory=list)
    exigir_verificado: bool = False
    cor_destaque: str = "lilas"
    avatar_url: str | None = None
    verificado: bool
    nota_media: float | None = None
    total_avaliacoes: int = 0
    adicionais_pagos: int = 0
    destaque: bool = False
    vagas_publicadas: int = 0
    servicos_concluidos: int = 0
    membro_desde: datetime
    avaliacoes: list[AvaliacaoPublica] = Field(default_factory=list)
