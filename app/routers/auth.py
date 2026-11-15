from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import Usuario
from app.schemas import Token, UserCreate, UserLogin, UserPublic
from app.security import create_access_token, hash_password, verify_password

router = APIRouter(tags=["auth"])


def _publico(usuario: Usuario) -> UserPublic:
    perfil = usuario.perfil_profissional
    return UserPublic(
        id=usuario.id,
        nome=usuario.nome,
        email=usuario.email,
        tipo=usuario.perfil_ativo.value if usuario.perfil_ativo else "usuario",
        verificado=bool(perfil and perfil.verificado),
    )


@router.post("/register", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: Session = Depends(get_db)) -> UserPublic:
    existing_user = db.scalar(select(Usuario).where(Usuario.email == str(payload.email).lower()))
    if existing_user is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="E-mail já cadastrado")

    user = Usuario(
        nome=payload.nome.strip(),
        email=str(payload.email).lower(),
        senha_hash=hash_password(payload.senha),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _publico(user)


@router.post("/login", response_model=Token)
def login(payload: UserLogin, db: Session = Depends(get_db)) -> Token:
    email = str(payload.email).lower()
    user = db.scalar(select(Usuario).where(Usuario.email == email))
    if user is None or not verify_password(payload.senha, user.senha_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="E-mail ou senha inválidos",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return Token(access_token=create_access_token(str(user.id)))


@router.get("/me", response_model=UserPublic)
def read_current_user(current_user: Usuario = Depends(get_current_user)) -> UserPublic:
    return _publico(current_user)
