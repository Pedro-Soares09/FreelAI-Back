from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.v1.schemas.auth import (
    LoginEntrada,
    ParDeTokens,
    PerfilAtivoEntrada,
    RegistroEntrada,
    RenovacaoEntrada,
    UsuarioAtual,
)
from app.db import get_db
from app.deps import usuario_atual
from app.models import Usuario
from app.services import autenticacao

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/registro", response_model=UsuarioAtual, status_code=status.HTTP_201_CREATED)
def registrar(entrada: RegistroEntrada, db: Session = Depends(get_db)) -> UsuarioAtual:
    """Cria a conta. Ela nasce pendente e é ativada depois da verificação de identidade."""
    usuario = autenticacao.registrar_usuario(db, entrada.nome, entrada.email, entrada.senha)
    return autenticacao.montar_usuario_atual(usuario)


@router.post("/login", response_model=ParDeTokens)
def entrar(entrada: LoginEntrada, db: Session = Depends(get_db)) -> ParDeTokens:
    usuario = autenticacao.autenticar(db, entrada.email, entrada.senha)
    return autenticacao.emitir_tokens(usuario)


@router.post("/refresh", response_model=ParDeTokens)
def renovar(entrada: RenovacaoEntrada, db: Session = Depends(get_db)) -> ParDeTokens:
    """Troca um token de renovação válido por um novo par de tokens."""
    return autenticacao.renovar_tokens(db, entrada.refresh_token)


@router.get("/me", response_model=UsuarioAtual)
def consultar_conta(usuario: Usuario = Depends(usuario_atual)) -> UsuarioAtual:
    return autenticacao.montar_usuario_atual(usuario)


@router.put("/perfil-ativo", response_model=UsuarioAtual)
def alternar_perfil(
    entrada: PerfilAtivoEntrada,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> UsuarioAtual:
    """Alterna entre os perfis profissional e contratante da mesma conta."""
    usuario = autenticacao.definir_perfil_ativo(db, usuario, entrada.perfil)
    return autenticacao.montar_usuario_atual(usuario)
