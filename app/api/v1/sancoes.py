"""Sanções de conta: situação e contestação (sem login) e ações manuais da administração."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.schemas.auth import LoginEntrada
from app.db import get_db
from app.deps import exige_admin
from app.erros import erro_http
from app.models import ContestacaoConta, EstadoConta, SancaoConta, Usuario
from app.services import autenticacao, sancoes
from app.tempo import como_utc

# Conta suspensa ou banida não recebe token; por isso a consulta e a contestação confirmam
# a identidade com e-mail e senha.
router_conta = APIRouter(prefix="/auth", tags=["sancoes"])
router_admin = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(exige_admin)])


class SancaoDetalhe(BaseModel):
    id: int
    tipo: str
    motivo: str
    inicio_em: datetime
    retorno_em: datetime | None = None
    contestavel_ate: datetime
    pode_contestar: bool


class ContestacaoDetalhe(BaseModel):
    texto: str
    criada_em: datetime
    decidida_em: datetime | None = None
    aceita: bool | None = None
    motivo_decisao: str | None = None


class SituacaoDaConta(BaseModel):
    estado: str
    sancao: SancaoDetalhe | None = None
    contestacao: ContestacaoDetalhe | None = None


class ContestacaoEntrada(LoginEntrada):
    texto: str = Field(min_length=10, max_length=1000)


def _usuario_por_credenciais(db: Session, entrada: LoginEntrada) -> Usuario:
    return autenticacao.verificar_credenciais(db, entrada.email, entrada.senha)


@router_conta.post("/sancao", response_model=SituacaoDaConta)
def consultar_sancao(entrada: LoginEntrada, db: Session = Depends(get_db)) -> dict:
    """Situação da conta bloqueada: motivo, data de retorno e andamento da contestação."""
    return sancoes.situacao(db, _usuario_por_credenciais(db, entrada))


@router_conta.post(
    "/contestacao", response_model=SituacaoDaConta, status_code=status.HTTP_201_CREATED
)
def contestar_sancao(entrada: ContestacaoEntrada, db: Session = Depends(get_db)) -> dict:
    """Envia a contestação da sanção vigente (uma por caso, dentro do prazo)."""
    usuario = _usuario_por_credenciais(db, entrada)
    sancoes.contestar(db, usuario, entrada.texto)
    return sancoes.situacao(db, usuario)


# --------------------------------------------------------------------------- administração


class SancaoManualEntrada(BaseModel):
    motivo: str = Field(min_length=3, max_length=255)
    dias: int = Field(default=30, ge=1, le=365)


class MotivoEntrada(BaseModel):
    motivo: str = Field(min_length=3, max_length=255)


class SancaoDaFila(BaseModel):
    id: int
    usuario_id: int
    usuario_nome: str
    usuario_email: str
    tipo: str
    origem: str
    motivo: str
    inicio_em: datetime
    retorno_em: datetime | None = None
    ativa: bool
    contestada: bool


class ContestacaoDaFila(BaseModel):
    id: int
    usuario_id: int
    usuario_nome: str
    sancao_tipo: str
    sancao_motivo: str
    texto: str
    criada_em: datetime
    decidida_em: datetime | None = None
    aceita: bool | None = None
    motivo_decisao: str | None = None


class DecisaoContestacaoEntrada(BaseModel):
    aceita: bool
    motivo: str = Field(min_length=3, max_length=255)


def _usuario(db: Session, usuario_id: int) -> Usuario:
    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        raise erro_http(
            status.HTTP_404_NOT_FOUND, "usuario_nao_encontrado", "Usuário não encontrado."
        )
    return usuario


def _item_sancao(db: Session, sancao: SancaoConta) -> SancaoDaFila:
    usuario = db.get(Usuario, sancao.usuario_id)
    contestada = (
        db.scalar(select(ContestacaoConta.id).where(ContestacaoConta.sancao_id == sancao.id))
        is not None
    )
    return SancaoDaFila(
        id=sancao.id,
        usuario_id=sancao.usuario_id,
        usuario_nome=usuario.nome,
        usuario_email=usuario.email,
        tipo=sancao.tipo,
        origem=sancao.origem,
        motivo=sancao.motivo,
        inicio_em=como_utc(sancao.inicio_em),
        retorno_em=como_utc(sancao.fim_em) if sancao.fim_em else None,
        ativa=sancao.ativa,
        contestada=contestada,
    )


@router_admin.get("/sancoes", response_model=list[SancaoDaFila])
def listar_sancoes(
    ativas: bool = Query(default=True), db: Session = Depends(get_db)
) -> list[SancaoDaFila]:
    """Suspensões e banimentos; por padrão só os vigentes."""
    consulta = select(SancaoConta).order_by(SancaoConta.id.desc())
    if ativas:
        consulta = consulta.where(SancaoConta.ativa.is_(True))
    return [_item_sancao(db, s) for s in db.scalars(consulta)]


@router_admin.post("/usuarios/{usuario_id}/suspensao", response_model=SancaoDaFila)
def suspender_usuario(
    usuario_id: int, entrada: SancaoManualEntrada, db: Session = Depends(get_db)
) -> SancaoDaFila:
    """Suspende a conta por alguns dias, com motivo."""
    usuario = _usuario(db, usuario_id)
    sancao = sancoes.suspender(
        db, usuario, dias=entrada.dias, motivo=entrada.motivo, origem="admin"
    )
    return _item_sancao(db, sancao)


@router_admin.post("/usuarios/{usuario_id}/banimento", response_model=SancaoDaFila)
def banir_usuario(
    usuario_id: int, entrada: MotivoEntrada, db: Session = Depends(get_db)
) -> SancaoDaFila:
    """Bane a conta de forma permanente, com motivo."""
    usuario = _usuario(db, usuario_id)
    return _item_sancao(db, sancoes.banir(db, usuario, motivo=entrada.motivo, origem="admin"))


@router_admin.post("/usuarios/{usuario_id}/reativacao", status_code=status.HTTP_204_NO_CONTENT)
def reativar_usuario(usuario_id: int, db: Session = Depends(get_db)) -> None:
    """Encerra a sanção vigente e devolve a conta ao estado ativo."""
    usuario = _usuario(db, usuario_id)
    if usuario.estado not in (EstadoConta.SUSPENSA, EstadoConta.BANIDA):
        raise erro_http(
            status.HTTP_409_CONFLICT, "conta_sem_sancao", "A conta não está suspensa nem banida."
        )
    sancoes.reativar(db, usuario)


def _item_contestacao(db: Session, c: ContestacaoConta) -> ContestacaoDaFila:
    usuario = db.get(Usuario, c.usuario_id)
    sancao = db.get(SancaoConta, c.sancao_id)
    return ContestacaoDaFila(
        id=c.id,
        usuario_id=c.usuario_id,
        usuario_nome=usuario.nome,
        sancao_tipo=sancao.tipo,
        sancao_motivo=sancao.motivo,
        texto=c.texto,
        criada_em=como_utc(c.criada_em),
        decidida_em=como_utc(c.decidida_em) if c.decidida_em else None,
        aceita=c.aceita,
        motivo_decisao=c.motivo_decisao,
    )


@router_admin.get("/contestacoes", response_model=list[ContestacaoDaFila])
def listar_contestacoes(
    estado: Literal["abertas", "decididas"] = Query(default="abertas"),
    db: Session = Depends(get_db),
) -> list[ContestacaoDaFila]:
    consulta = select(ContestacaoConta).order_by(ContestacaoConta.id)
    if estado == "abertas":
        consulta = consulta.where(ContestacaoConta.decidida_em.is_(None))
    else:
        consulta = consulta.where(ContestacaoConta.decidida_em.is_not(None))
    return [_item_contestacao(db, c) for c in db.scalars(consulta)]


@router_admin.post("/contestacoes/{contestacao_id}/decisao", response_model=ContestacaoDaFila)
def decidir_contestacao(
    contestacao_id: int, entrada: DecisaoContestacaoEntrada, db: Session = Depends(get_db)
) -> ContestacaoDaFila:
    """Aceita (reativa a conta) ou recusa a contestação, com motivo."""
    contestacao = sancoes.decidir_contestacao(
        db, contestacao_id, aceita=entrada.aceita, motivo=entrada.motivo
    )
    return _item_contestacao(db, contestacao)
