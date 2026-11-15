"""Regras de conta: registro, login, renovação de tokens e perfil ativo."""

from datetime import timedelta

from fastapi import status
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.schemas.auth import ParDeTokens, UsuarioAtual
from app.config import settings
from app.deps import barrar_conta_bloqueada
from app.erros import erro_http
from app.models import PerfilContratante, PerfilProfissional, TipoPerfil, Usuario
from app.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.tempo import como_utc


def _normalizar_email(email: str) -> str:
    return email.strip().lower()


def registrar_usuario(db: Session, nome: str, email: str, senha: str) -> Usuario:
    email = _normalizar_email(email)
    if db.scalar(select(Usuario).where(Usuario.email == email)) is not None:
        raise erro_http(status.HTTP_409_CONFLICT, "email_em_uso", "E-mail já cadastrado.")
    usuario = Usuario(nome=nome.strip(), email=email, senha_hash=hash_password(senha))
    db.add(usuario)
    db.commit()
    db.refresh(usuario)
    return usuario


# Hash de uma senha inexistente, verificado quando o e-mail não está cadastrado para que o tempo
# de resposta não revele se a conta existe.
_HASH_FICTICIO = hash_password("senha-fictícia-para-igualar-o-tempo")


def autenticar(db: Session, email: str, senha: str) -> Usuario:
    usuario = db.scalar(select(Usuario).where(Usuario.email == _normalizar_email(email)))
    hash_conferido = usuario.senha_hash if usuario is not None else _HASH_FICTICIO
    senha_confere = verify_password(senha, hash_conferido)
    if usuario is None or not senha_confere:
        raise erro_http(
            status.HTTP_401_UNAUTHORIZED, "credenciais_invalidas", "E-mail ou senha inválidos."
        )
    barrar_conta_bloqueada(usuario, db)
    return usuario


def emitir_tokens(usuario: Usuario) -> ParDeTokens:
    return ParDeTokens(
        access_token=create_access_token(str(usuario.id)),
        refresh_token=create_refresh_token(str(usuario.id)),
        expira_em_segundos=int(
            timedelta(minutes=settings.access_token_expire_minutes).total_seconds()
        ),
    )


def renovar_tokens(db: Session, refresh_token: str) -> ParDeTokens:
    try:
        carga = decode_token(refresh_token, "refresh")
        usuario = db.get(Usuario, int(carga["sub"]))
    except (JWTError, KeyError, ValueError):
        usuario = None
    if usuario is None:
        raise erro_http(
            status.HTTP_401_UNAUTHORIZED, "refresh_invalido", "Token de renovação inválido."
        )
    barrar_conta_bloqueada(usuario, db)
    return emitir_tokens(usuario)


def perfis_cadastrados(usuario: Usuario) -> list[TipoPerfil]:
    perfis = []
    if usuario.perfil_profissional is not None:
        perfis.append(TipoPerfil.PROFISSIONAL)
    if usuario.perfil_contratante is not None:
        perfis.append(TipoPerfil.CONTRATANTE)
    return perfis


def _perfil_do_tipo(
    usuario: Usuario, tipo: TipoPerfil | None
) -> PerfilProfissional | PerfilContratante | None:
    if tipo == TipoPerfil.PROFISSIONAL:
        return usuario.perfil_profissional
    if tipo == TipoPerfil.CONTRATANTE:
        return usuario.perfil_contratante
    return None


def montar_usuario_atual(usuario: Usuario) -> UsuarioAtual:
    perfil_ativo = _perfil_do_tipo(usuario, usuario.perfil_ativo)
    return UsuarioAtual(
        id=usuario.id,
        nome=usuario.nome,
        email=usuario.email,
        estado=usuario.estado,
        papel=usuario.papel,
        perfil_ativo=usuario.perfil_ativo,
        perfis=perfis_cadastrados(usuario),
        verificado=bool(perfil_ativo and perfil_ativo.verificado),
        suspensa_ate=como_utc(usuario.suspensa_ate) if usuario.suspensa_ate else None,
    )


def definir_perfil_ativo(db: Session, usuario: Usuario, tipo: TipoPerfil) -> Usuario:
    if tipo not in perfis_cadastrados(usuario):
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "perfil_nao_cadastrado",
            f"O perfil {tipo.value} ainda não foi cadastrado nesta conta.",
        )
    usuario.perfil_ativo = tipo
    db.commit()
    db.refresh(usuario)
    return usuario


def exigir_nao_autocontratacao(
    contratante: PerfilContratante, profissional: PerfilProfissional
) -> None:
    """Uma mesma conta não pode ocupar os dois lados de uma contratação."""
    if contratante.usuario_id == profissional.usuario_id:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "autocontratacao",
            "Não é possível contratar o próprio perfil.",
        )
