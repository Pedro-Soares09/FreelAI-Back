"""Tabelas centrais: contas, perfis, categorias, comprovações e configuração."""

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import (
    EstadoConta,
    PapelUsuario,
    RiscoCategoria,
    StatusAprovacao,
    TipoPerfil,
    TipoPessoa,
    TipoQualificacao,
)
from app.tempo import agora


def _enum(tipo):
    """Enum armazenado como texto, portável entre PostgreSQL e SQLite."""
    return Enum(tipo, native_enum=False, length=32, values_callable=lambda e: [m.value for m in e])


class MarcaTempo:
    """Colunas de auditoria compartilhadas."""

    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=agora, onupdate=agora
    )


class Usuario(MarcaTempo, Base):
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    senha_hash: Mapped[str] = mapped_column(String(255))
    estado: Mapped[EstadoConta] = mapped_column(_enum(EstadoConta), default=EstadoConta.PENDENTE)
    papel: Mapped[PapelUsuario] = mapped_column(_enum(PapelUsuario), default=PapelUsuario.USUARIO)
    perfil_ativo: Mapped[TipoPerfil | None] = mapped_column(_enum(TipoPerfil), nullable=True)
    suspensa_ate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    perfil_profissional: Mapped["PerfilProfissional | None"] = relationship(
        back_populates="usuario", uselist=False
    )
    perfil_contratante: Mapped["PerfilContratante | None"] = relationship(
        back_populates="usuario", uselist=False
    )


class Endereco(Base):
    __tablename__ = "enderecos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    logradouro: Mapped[str | None] = mapped_column(String(160), nullable=True)
    numero: Mapped[str | None] = mapped_column(String(20), nullable=True)
    complemento: Mapped[str | None] = mapped_column(String(80), nullable=True)
    bairro: Mapped[str] = mapped_column(String(80))
    cidade: Mapped[str] = mapped_column(String(80), default="Recife")
    uf: Mapped[str] = mapped_column(String(2), default="PE")
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)


categorias_profissional = Table(
    "categorias_profissional",
    Base.metadata,
    Column("profissional_id", ForeignKey("perfis_profissionais.id"), primary_key=True),
    Column("categoria_id", ForeignKey("categorias.id"), primary_key=True),
)


class PerfilProfissional(MarcaTempo, Base):
    __tablename__ = "perfis_profissionais"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), unique=True)
    cpf: Mapped[str] = mapped_column(String(11), unique=True)
    data_nascimento: Mapped[date] = mapped_column(Date)
    telefone: Mapped[str] = mapped_column(String(15))
    foto_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    endereco_id: Mapped[int | None] = mapped_column(ForeignKey("enderecos.id"), nullable=True)
    raio_km: Mapped[int] = mapped_column(SmallInteger, default=5)
    disponivel_ate: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    servicos_concluidos: Mapped[int] = mapped_column(Integer, default=0)
    comunicacao: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    agilidade: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    organizacao: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    verificacao: Mapped[StatusAprovacao] = mapped_column(
        _enum(StatusAprovacao), default=StatusAprovacao.PENDENTE
    )
    verificacao_motivo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    experiencia_aprovada: Mapped[StatusAprovacao] = mapped_column(
        _enum(StatusAprovacao), default=StatusAprovacao.PENDENTE
    )

    usuario: Mapped[Usuario] = relationship(back_populates="perfil_profissional")
    endereco: Mapped[Endereco | None] = relationship()
    categorias: Mapped[list["Categoria"]] = relationship(secondary=categorias_profissional)

    @property
    def verificado(self) -> bool:
        return self.verificacao == StatusAprovacao.APROVADO


class PerfilContratante(MarcaTempo, Base):
    __tablename__ = "perfis_contratantes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), unique=True)
    tipo_pessoa: Mapped[TipoPessoa] = mapped_column(_enum(TipoPessoa))
    documento: Mapped[str | None] = mapped_column(String(14), nullable=True)
    razao_social: Mapped[str | None] = mapped_column(String(160), nullable=True)
    telefone: Mapped[str] = mapped_column(String(15))
    selfie_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    verificacao: Mapped[StatusAprovacao] = mapped_column(
        _enum(StatusAprovacao), default=StatusAprovacao.PENDENTE
    )

    usuario: Mapped[Usuario] = relationship(back_populates="perfil_contratante")

    @property
    def verificado(self) -> bool:
        return self.verificacao == StatusAprovacao.APROVADO


class Categoria(Base):
    __tablename__ = "categorias"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(40), unique=True)
    nome: Mapped[str] = mapped_column(String(80))
    risco: Mapped[RiscoCategoria] = mapped_column(_enum(RiscoCategoria))
    exige_qualificacao: Mapped[bool] = mapped_column(Boolean, default=False)
    exige_epi: Mapped[bool] = mapped_column(Boolean, default=False)

    epis: Mapped[list["EpiCategoria"]] = relationship(back_populates="categoria")
    checklist: Mapped[list["ChecklistItem"]] = relationship(back_populates="categoria")


class EpiCategoria(Base):
    """Equipamento de proteção individual esperado para uma categoria."""

    __tablename__ = "epis_categoria"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    categoria_id: Mapped[int] = mapped_column(ForeignKey("categorias.id"))
    descricao: Mapped[str] = mapped_column(String(120))

    categoria: Mapped[Categoria] = relationship(back_populates="epis")


class ChecklistItem(Base):
    """Pergunta do checklist de segurança de uma categoria."""

    __tablename__ = "checklist_itens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    categoria_id: Mapped[int] = mapped_column(ForeignKey("categorias.id"))
    texto: Mapped[str] = mapped_column(String(200))
    critico: Mapped[bool] = mapped_column(Boolean, default=False)

    categoria: Mapped[Categoria] = relationship(back_populates="checklist")


class Experiencia(Base):
    __tablename__ = "experiencias"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profissional_id: Mapped[int] = mapped_column(ForeignKey("perfis_profissionais.id"))
    categoria_id: Mapped[int | None] = mapped_column(ForeignKey("categorias.id"), nullable=True)
    descricao: Mapped[str] = mapped_column(Text)
    referencia_nome: Mapped[str | None] = mapped_column(String(120), nullable=True)
    referencia_contato: Mapped[str | None] = mapped_column(String(80), nullable=True)
    arquivo_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[StatusAprovacao] = mapped_column(
        _enum(StatusAprovacao), default=StatusAprovacao.PENDENTE
    )


class Qualificacao(Base):
    __tablename__ = "qualificacoes"
    __table_args__ = (UniqueConstraint("profissional_id", "categoria_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profissional_id: Mapped[int] = mapped_column(ForeignKey("perfis_profissionais.id"))
    categoria_id: Mapped[int] = mapped_column(ForeignKey("categorias.id"))
    tipo: Mapped[TipoQualificacao] = mapped_column(_enum(TipoQualificacao))
    arquivo_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    referencia: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[StatusAprovacao] = mapped_column(
        _enum(StatusAprovacao), default=StatusAprovacao.PENDENTE
    )
    motivo_recusa: Mapped[str | None] = mapped_column(String(255), nullable=True)


class EpiProfissional(Base):
    """Foto de EPI enviada pelo profissional para uma categoria."""

    __tablename__ = "epis_profissional"
    __table_args__ = (UniqueConstraint("profissional_id", "categoria_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profissional_id: Mapped[int] = mapped_column(ForeignKey("perfis_profissionais.id"))
    categoria_id: Mapped[int] = mapped_column(ForeignKey("categorias.id"))
    arquivo_url: Mapped[str] = mapped_column(String(255))
    status: Mapped[StatusAprovacao] = mapped_column(
        _enum(StatusAprovacao), default=StatusAprovacao.PENDENTE
    )
    motivo_recusa: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Configuracao(Base):
    """Parâmetro ajustável da plataforma, por perfil de execução."""

    __tablename__ = "configuracoes"
    __table_args__ = (UniqueConstraint("chave", "perfil"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chave: Mapped[str] = mapped_column(String(80))
    perfil: Mapped[str] = mapped_column(String(16))
    valor: Mapped[str] = mapped_column(String(80))
    descricao: Mapped[str | None] = mapped_column(String(200), nullable=True)
