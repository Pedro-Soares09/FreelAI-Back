from datetime import timedelta
from typing import Literal

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings
from app.tempo import agora

password_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

TipoToken = Literal["access", "refresh"]


def hash_password(password: str) -> str:
    return password_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return password_context.verify(password, password_hash)


def _criar_token(subject: str, tipo: TipoToken, duracao: timedelta) -> str:
    emitido_em = agora()
    return jwt.encode(
        {"sub": subject, "tipo": tipo, "iat": emitido_em, "exp": emitido_em + duracao},
        settings.secret_key,
        algorithm=settings.algorithm,
    )


def create_access_token(subject: str, duracao: timedelta | None = None) -> str:
    """Token de acesso, de vida curta, enviado em cada requisição."""
    return _criar_token(
        subject, "access", duracao or timedelta(minutes=settings.access_token_expire_minutes)
    )


def create_refresh_token(subject: str, duracao: timedelta | None = None) -> str:
    """Token de renovação, de vida longa, usado apenas para obter um novo par de tokens."""
    return _criar_token(
        subject, "refresh", duracao or timedelta(minutes=settings.refresh_token_expire_minutes)
    )


def decode_token(token: str, tipo: TipoToken) -> dict:
    """Valida assinatura, validade e tipo do token. Levanta `JWTError` se algo falhar."""
    payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    if payload.get("tipo") != tipo:
        raise JWTError("Tipo de token inesperado")
    return payload


def decode_access_token(token: str) -> dict:
    return decode_token(token, "access")


__all__ = [
    "JWTError",
    "create_access_token",
    "create_refresh_token",
    "decode_access_token",
    "decode_token",
    "hash_password",
    "verify_password",
]
