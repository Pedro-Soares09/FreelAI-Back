"""Tabelas de contratação: vagas, convites, contrato, pagamento, chat e pós-serviço."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import (
    EstadoConvite,
    EstadoDenuncia,
    EstadoDisputa,
    EstadoPagamento,
    EstadoVaga,
    FinalidadeAceite,
    OrigemDenuncia,
    TipoLancamento,
    TipoPerfil,
    TipoVaga,
)
from app.models.nucleo import MarcaTempo, _enum
from app.tempo import agora


class Vaga(MarcaTempo, Base):
    __tablename__ = "vagas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contratante_id: Mapped[int] = mapped_column(ForeignKey("perfis_contratantes.id"), index=True)
    categoria_id: Mapped[int] = mapped_column(ForeignKey("categorias.id"))
    profissional_id: Mapped[int | None] = mapped_column(
        ForeignKey("perfis_profissionais.id"), nullable=True
    )
    endereco_id: Mapped[int] = mapped_column(ForeignKey("enderecos.id"))
    titulo: Mapped[str] = mapped_column(String(120))
    descricao: Mapped[str] = mapped_column(Text)
    tipo: Mapped[TipoVaga] = mapped_column(_enum(TipoVaga), default=TipoVaga.AVULSO)
    inicio_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duracao_minutos: Mapped[int] = mapped_column(Integer)
    valor_inicial: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    valor_fixado: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    vestimenta: Mapped[str | None] = mapped_column(String(160), nullable=True)
    atribuicoes: Mapped[str | None] = mapped_column(Text, nullable=True)
    condicoes_pagamento: Mapped[str | None] = mapped_column(String(200), nullable=True)
    estado: Mapped[EstadoVaga] = mapped_column(
        _enum(EstadoVaga), default=EstadoVaga.PUBLICADA, index=True
    )
    rodada: Mapped[int] = mapped_column(SmallInteger, default=1)
    prazo_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    convites: Mapped[list["Convite"]] = relationship(back_populates="vaga")


class Convite(MarcaTempo, Base):
    """Convite a um dos profissionais selecionados para uma vaga."""

    __tablename__ = "convites"
    __table_args__ = (UniqueConstraint("vaga_id", "profissional_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vaga_id: Mapped[int] = mapped_column(ForeignKey("vagas.id"), index=True)
    profissional_id: Mapped[int] = mapped_column(ForeignKey("perfis_profissionais.id"))
    rodada: Mapped[int] = mapped_column(SmallInteger, default=1)
    posicao: Mapped[int] = mapped_column(SmallInteger)
    pontuacao: Mapped[float] = mapped_column(default=0.0)
    estado: Mapped[EstadoConvite] = mapped_column(
        _enum(EstadoConvite), default=EstadoConvite.SUGERIDO
    )
    valor_proposto: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    expira_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    vaga: Mapped[Vaga] = relationship(back_populates="convites")


class Contrato(Base):
    __tablename__ = "contratos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vaga_id: Mapped[int] = mapped_column(ForeignKey("vagas.id"), unique=True)
    versao_termos: Mapped[str] = mapped_column(String(20))
    conteudo: Mapped[dict] = mapped_column(JSON)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)

    aceites: Mapped[list["AceiteContrato"]] = relationship(back_populates="contrato")


class AceiteContrato(Base):
    __tablename__ = "aceites_contrato"
    __table_args__ = (UniqueConstraint("contrato_id", "perfil"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contrato_id: Mapped[int] = mapped_column(ForeignKey("contratos.id"))
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    perfil: Mapped[TipoPerfil] = mapped_column(_enum(TipoPerfil))
    aceito_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)

    contrato: Mapped[Contrato] = relationship(back_populates="aceites")


class Pagamento(MarcaTempo, Base):
    __tablename__ = "pagamentos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vaga_id: Mapped[int] = mapped_column(ForeignKey("vagas.id"), unique=True)
    valor_total: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    estado: Mapped[EstadoPagamento] = mapped_column(
        _enum(EstadoPagamento), default=EstadoPagamento.AGUARDANDO
    )
    codigo_pix: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expira_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lancamentos: Mapped[list["Lancamento"]] = relationship(back_populates="pagamento")


class Lancamento(Base):
    """Movimentação financeira. Somente inserção: nunca é alterada nem removida."""

    __tablename__ = "lancamentos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pagamento_id: Mapped[int] = mapped_column(ForeignKey("pagamentos.id"), index=True)
    tipo: Mapped[TipoLancamento] = mapped_column(_enum(TipoLancamento))
    valor: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    descricao: Mapped[str | None] = mapped_column(String(160), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)

    pagamento: Mapped[Pagamento] = relationship(back_populates="lancamentos")


@event.listens_for(Lancamento, "before_update")
def _bloquear_alteracao(*_args) -> None:
    raise RuntimeError("Lançamentos são imutáveis: registre um novo lançamento de ajuste.")


@event.listens_for(Lancamento, "before_delete")
def _bloquear_remocao(*_args) -> None:
    raise RuntimeError("Lançamentos são imutáveis: não podem ser removidos.")


class Conversa(Base):
    __tablename__ = "conversas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vaga_id: Mapped[int] = mapped_column(ForeignKey("vagas.id"), unique=True)
    aberta: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)

    mensagens: Mapped[list["Mensagem"]] = relationship(back_populates="conversa")


class Mensagem(Base):
    __tablename__ = "mensagens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversa_id: Mapped[int] = mapped_column(ForeignKey("conversas.id"), index=True)
    autor_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    texto: Mapped[str | None] = mapped_column(Text, nullable=True)
    anexo_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enviada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    lida_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    conversa: Mapped[Conversa] = relationship(back_populates="mensagens")


class Avaliacao(Base):
    __tablename__ = "avaliacoes"
    __table_args__ = (UniqueConstraint("vaga_id", "avaliador_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vaga_id: Mapped[int] = mapped_column(ForeignKey("vagas.id"))
    avaliador_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    avaliado_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    nota: Mapped[int] = mapped_column(SmallInteger)
    comentario: Mapped[str | None] = mapped_column(Text, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    visivel_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Denuncia(Base):
    __tablename__ = "denuncias"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    denunciante_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    denunciado_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    vaga_id: Mapped[int | None] = mapped_column(ForeignKey("vagas.id"), nullable=True)
    conversa_id: Mapped[int | None] = mapped_column(ForeignKey("conversas.id"), nullable=True)
    origem: Mapped[OrigemDenuncia] = mapped_column(
        _enum(OrigemDenuncia), default=OrigemDenuncia.USUARIO
    )
    motivo: Mapped[str] = mapped_column(String(80))
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    estado: Mapped[EstadoDenuncia] = mapped_column(
        _enum(EstadoDenuncia), default=EstadoDenuncia.ABERTA
    )
    decisao_motivo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    decidido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Advertencia(Base):
    __tablename__ = "advertencias"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    vaga_id: Mapped[int | None] = mapped_column(ForeignKey("vagas.id"), nullable=True)
    motivo: Mapped[str] = mapped_column(String(120))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)


class Disputa(Base):
    __tablename__ = "disputas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vaga_id: Mapped[int] = mapped_column(ForeignKey("vagas.id"), unique=True)
    aberta_por_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    descricao: Mapped[str] = mapped_column(Text)
    estado: Mapped[EstadoDisputa] = mapped_column(
        _enum(EstadoDisputa), default=EstadoDisputa.ABERTA
    )
    percentual_reembolso: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    decisao_motivo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    decidido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Notificacao(Base):
    __tablename__ = "notificacoes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    tipo: Mapped[str] = mapped_column(String(40))
    titulo: Mapped[str] = mapped_column(String(120))
    mensagem: Mapped[str | None] = mapped_column(String(255), nullable=True)
    vaga_id: Mapped[int | None] = mapped_column(ForeignKey("vagas.id"), nullable=True)
    visivel_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    lida_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)


class AceiteTermos(Base):
    """Registro de aceite de termos ou consentimento, com data e versão."""

    __tablename__ = "aceites_termos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    finalidade: Mapped[FinalidadeAceite] = mapped_column(_enum(FinalidadeAceite))
    versao: Mapped[str] = mapped_column(String(20))
    aceito_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
