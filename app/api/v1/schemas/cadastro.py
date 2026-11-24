"""Entradas e saídas do cadastro dos perfis, da verificação de identidade e das comprovações."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import RiscoCategoria, StatusAprovacao, TipoPessoa, TipoQualificacao

EstadoVerificacao = Literal["pendente", "em_analise", "verificado", "recusado"]


def somente_digitos(valor: str) -> str:
    return "".join(c for c in valor if c.isdigit())


def cpf_valido(cpf: str) -> bool:
    """Confere os dois dígitos verificadores; rejeita sequências repetidas."""
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for tamanho in (9, 10):
        soma = sum(int(cpf[i]) * (tamanho + 1 - i) for i in range(tamanho))
        digito = (soma * 10 % 11) % 10
        if digito != int(cpf[tamanho]):
            return False
    return True


def cnpj_valido(cnpj: str) -> bool:
    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        return False
    for tamanho, pesos in (
        (12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]),
        (13, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]),
    ):
        soma = sum(int(cnpj[i]) * pesos[i] for i in range(tamanho))
        digito = 0 if soma % 11 < 2 else 11 - soma % 11
        if digito != int(cnpj[tamanho]):
            return False
    return True


def _telefone(valor: str) -> str:
    digitos = somente_digitos(valor)
    if len(digitos) not in (10, 11):
        raise ValueError("Informe o telefone com DDD (10 ou 11 dígitos).")
    return digitos


class CaracteristicasEntrada(BaseModel):
    """Características autodeclaradas, de 1 a 5; informação complementar, sem peso no match."""

    comunicacao: int | None = Field(default=None, ge=1, le=5)
    agilidade: int | None = Field(default=None, ge=1, le=5)
    organizacao: int | None = Field(default=None, ge=1, le=5)


class ExperienciaEntrada(BaseModel):
    categoria: str | None = Field(default=None, description="Slug da categoria, se houver.")
    descricao: str = Field(min_length=1, max_length=2000)
    referencia_nome: str | None = Field(default=None, max_length=120)
    referencia_contato: str | None = Field(default=None, max_length=80)
    arquivo_url: str | None = Field(default=None, max_length=255)


class CadastroProfissionalEntrada(BaseModel):
    cpf: str
    data_nascimento: date
    telefone: str
    foto_url: str | None = Field(default=None, max_length=255)
    bairro: str = Field(min_length=1, max_length=80)
    logradouro: str | None = Field(default=None, max_length=160)
    latitude: float | None = Field(
        default=None, ge=-90, le=90, description="Opcional: deduzida do bairro quando ausente."
    )
    longitude: float | None = Field(default=None, ge=-180, le=180)
    raio_km: int = Field(default=5, ge=1)
    categorias: list[str] = Field(min_length=1, description="Slugs das categorias de atuação.")
    caracteristicas: CaracteristicasEntrada | None = None
    experiencias: list[ExperienciaEntrada] = Field(default_factory=list)

    @field_validator("cpf")
    @classmethod
    def _cpf(cls, valor: str) -> str:
        digitos = somente_digitos(valor)
        if not cpf_valido(digitos):
            raise ValueError("CPF inválido.")
        return digitos

    @field_validator("telefone")
    @classmethod
    def _tel(cls, valor: str) -> str:
        return _telefone(valor)

    @field_validator("data_nascimento")
    @classmethod
    def _maior_de_idade(cls, valor: date) -> date:
        hoje = date.today()
        idade = hoje.year - valor.year - ((hoje.month, hoje.day) < (valor.month, valor.day))
        if idade < 18:
            raise ValueError("É necessário ter 18 anos ou mais.")
        return valor


class AtualizacaoProfissionalEntrada(BaseModel):
    telefone: str | None = None
    foto_url: str | None = Field(default=None, max_length=255)
    raio_km: int | None = Field(default=None, ge=1)
    categorias: list[str] | None = Field(default=None, min_length=1)
    caracteristicas: CaracteristicasEntrada | None = None

    @field_validator("telefone")
    @classmethod
    def _tel(cls, valor: str | None) -> str | None:
        return None if valor is None else _telefone(valor)


class CadastroContratanteEntrada(BaseModel):
    tipo_pessoa: TipoPessoa
    documento: str | None = Field(default=None, description="CPF (pessoa física) ou CNPJ.")
    razao_social: str | None = Field(default=None, max_length=160)
    telefone: str
    selfie_url: str | None = Field(default=None, max_length=255)

    @field_validator("telefone")
    @classmethod
    def _tel(cls, valor: str) -> str:
        return _telefone(valor)

    @field_validator("documento")
    @classmethod
    def _doc(cls, valor: str | None) -> str | None:
        if valor is None or not valor.strip():
            return None
        return somente_digitos(valor)

    @model_validator(mode="after")
    def _documento_conforme_o_tipo(self):
        if self.tipo_pessoa == TipoPessoa.FISICA:
            if not self.documento or not cpf_valido(self.documento):
                raise ValueError("Informe um CPF válido para pessoa física.")
        elif self.documento is not None and not cnpj_valido(self.documento):
            raise ValueError("CNPJ inválido.")
        return self


class CategoriaDoPerfil(BaseModel):
    slug: str
    nome: str
    nivel_risco: RiscoCategoria
    pendencias: list[Literal["experiencia", "qualificacao", "epi"]] = Field(
        description="O que ainda falta aprovar para atuar na categoria."
    )


class PerfilProfissionalDetalhe(BaseModel):
    id: int
    usuario_id: int
    nome: str
    cpf: str
    data_nascimento: date
    telefone: str
    foto_url: str | None
    bairro: str | None
    latitude: float | None
    longitude: float | None
    raio_km: int
    categorias: list[CategoriaDoPerfil]
    caracteristicas: CaracteristicasEntrada
    verificacao: EstadoVerificacao
    verificacao_motivo: str | None
    experiencia_aprovada: StatusAprovacao
    pro: bool


class PerfilContratanteDetalhe(BaseModel):
    id: int
    usuario_id: int
    nome: str
    tipo_pessoa: TipoPessoa
    documento: str | None
    razao_social: str | None
    telefone: str
    selfie_url: str | None
    verificacao: EstadoVerificacao
    verificacao_motivo: str | None
    adicionais_pagos: int = 0
    destaque: bool = False
    bio: str | None = None
    bairro: str | None = None
    horario_contato: str | None = None
    categorias_interesse: list[str] = Field(default_factory=list)
    exigir_verificado: bool = False
    aceita_notificacoes: bool = True
    cor_destaque: str = "lilas"
    avatar_url: str | None = None


# ---------------------------------------------------------------- verificação


class VerificacaoEntrada(BaseModel):
    documento_arquivo_id: int = Field(description="Arquivo do tipo documento já enviado.")
    selfie_arquivo_id: int = Field(description="Arquivo do tipo selfie já enviado.")


class StatusVerificacao(BaseModel):
    perfil: Literal["profissional", "contratante"]
    status: EstadoVerificacao
    motivo: str | None = None
    consentimento_biometria: bool
    documento_enviado: bool
    selfie_enviada: bool
    enviada_em: datetime | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "perfil": "profissional",
                    "status": "em_analise",
                    "motivo": None,
                    "consentimento_biometria": True,
                    "documento_enviado": True,
                    "selfie_enviada": True,
                    "enviada_em": "2026-11-25T14:00:00Z",
                }
            ]
        }
    )


# ---------------------------------------------------------------- comprovações


class QualificacaoEntrada(BaseModel):
    categoria: str
    tipo: TipoQualificacao = TipoQualificacao.DECLARACAO
    arquivo_url: str | None = Field(default=None, max_length=255)
    referencia: str | None = Field(default=None, max_length=160)


class EpiEntrada(BaseModel):
    categoria: str
    arquivo_url: str = Field(max_length=255)


class ComprovacaoDetalhe(BaseModel):
    id: int
    tipo: Literal["experiencia", "qualificacao", "epi"]
    categoria: str | None
    descricao: str | None = None
    arquivo_url: str | None = None
    status: StatusAprovacao
    motivo_recusa: str | None = None


class ItemChecklist(BaseModel):
    id: int
    texto: str
    critico: bool


class SegurancaDaCategoria(BaseModel):
    slug: str
    nome: str
    nivel_risco: RiscoCategoria
    exige_qualificacao: bool
    exige_epi: bool
    epis: list[str]
    checklist: list[ItemChecklist]


# ---------------------------------------------------------------- admin


class ArquivoDaFila(BaseModel):
    tipo: str
    url: str


class ItemFilaAprovacao(BaseModel):
    tipo: Literal["profissional", "contratante", "experiencia", "qualificacao", "epi"]
    id: int
    usuario_id: int
    usuario_nome: str
    descricao: str
    status: StatusAprovacao
    enviado_em: datetime | None = None
    arquivos: list[ArquivoDaFila] = Field(default_factory=list)


class DecisaoAprovacaoEntrada(BaseModel):
    decisao: Literal["aprovar", "recusar"]
    motivo: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def _recusa_exige_motivo(self):
        if self.decisao == "recusar" and not (self.motivo and self.motivo.strip()):
            raise ValueError("Informe o motivo da recusa.")
        return self
