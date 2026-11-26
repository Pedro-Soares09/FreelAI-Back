"""Plano Pro simulado, termos de uso versionados e exclusão da conta."""

from datetime import datetime

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import usuario_atual
from app.models import Usuario
from app.services import conta

router = APIRouter(tags=["conta"])


class AssinaturaProDetalhe(BaseModel):
    ativo: bool
    valor_mensal: float
    valido_ate: datetime | None = None
    renovacao_automatica: bool


class SituacaoTermos(BaseModel):
    versao_atual: str
    aceito: bool


class AceiteTermosEntrada(BaseModel):
    versao: str = Field(min_length=1, max_length=20)


class ExclusaoContaEntrada(BaseModel):
    senha: str = Field(min_length=1, max_length=72)


@router.get("/assinatura/pro", response_model=AssinaturaProDetalhe)
def consultar_assinatura(
    usuario: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)
) -> dict:
    """Situação do plano Pro e o valor mensal (simulado)."""
    return conta.situacao_pro(db, usuario)


@router.post("/assinatura/pro/assinar", response_model=AssinaturaProDetalhe)
def assinar(usuario: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)) -> dict:
    """Ativa o plano Pro: notificações imediatas e selo; não altera a pontuação do match."""
    return conta.assinar_pro(db, usuario)


@router.post("/assinatura/pro/cancelar", response_model=AssinaturaProDetalhe)
def cancelar(usuario: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)) -> dict:
    """Cancela a renovação; o plano segue valendo até o fim do período pago."""
    return conta.cancelar_pro(db, usuario)


@router.get("/termos", response_model=SituacaoTermos)
def consultar_termos(
    usuario: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)
) -> dict:
    """Versão atual dos termos e se a conta já a aceitou."""
    return conta.situacao_termos(db, usuario)


@router.post("/termos/aceite", response_model=SituacaoTermos)
def aceitar_termos(
    entrada: AceiteTermosEntrada,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> dict:
    """Registra o aceite da versão atual dos termos, com data e hora."""
    return conta.aceitar_termos(db, usuario, entrada.versao)


@router.post("/conta/exclusao", status_code=status.HTTP_204_NO_CONTENT)
def excluir_conta(
    entrada: ExclusaoContaEntrada,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> None:
    """Exclui a conta (anonimiza os dados pessoais) depois de confirmar a senha."""
    conta.excluir_conta(db, usuario, entrada.senha)
