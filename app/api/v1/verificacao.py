"""Verificação de identidade: consentimento de biometria, envio de documento e selfie, status."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.v1.schemas.cadastro import StatusVerificacao, VerificacaoEntrada
from app.db import get_db
from app.deps import usuario_atual
from app.models import Usuario
from app.services import cadastro

router = APIRouter(prefix="/verificacao", tags=["verificacao"])


@router.get("/status", response_model=StatusVerificacao)
def consultar_status(
    usuario: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)
) -> StatusVerificacao:
    """Estado da verificação do perfil ativo: pendente, em análise, verificado ou recusado."""
    return cadastro.status_da_verificacao(db, usuario)


@router.post("/consentimento", response_model=StatusVerificacao)
def aceitar_consentimento(
    usuario: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)
) -> StatusVerificacao:
    """Registra o consentimento de uso de biometria (LGPD); idempotente."""
    return cadastro.registrar_consentimento(db, usuario)


@router.post("/envio", response_model=StatusVerificacao)
def enviar_verificacao(
    entrada: VerificacaoEntrada,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> StatusVerificacao:
    """Envia o documento e a selfie (já enviados em /arquivos) para análise da administração."""
    return cadastro.enviar_verificacao(db, usuario, entrada)
