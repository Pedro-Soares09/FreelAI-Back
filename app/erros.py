"""Construção padronizada de erros HTTP com código estável para o front."""

from typing import Any

from fastapi import HTTPException


def erro_http(status: int, codigo: str, mensagem: str, **extras: Any) -> HTTPException:
    """Cria uma `HTTPException` cujo `detail` traz `codigo`, `mensagem` e dados extras."""
    return HTTPException(
        status_code=status,
        detail={"codigo": codigo, "mensagem": mensagem, **extras},
    )
