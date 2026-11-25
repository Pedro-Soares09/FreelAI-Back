"""Construção padronizada de erros HTTP, exceções de domínio e tradução Pydantic."""

from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ErroDominio(HTTPException):
    """Base para erros de regra/estado de domínio no formato padronizado."""

    status_padrao: int = status.HTTP_400_BAD_REQUEST
    codigo_padrao: str = "erro_dominio"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: Any = None,
    ) -> None:
        codigo = code or self.codigo_padrao
        http_status = status_code if status_code is not None else self.status_padrao
        payload_detail: dict[str, Any] = {"codigo": codigo, "mensagem": message}
        if isinstance(details, dict):
            payload_detail.update(details)
        super().__init__(
            status_code=http_status,
            detail=payload_detail,
        )
        self.code = codigo
        self.message = message
        self.details = details


class NotFound(ErroDominio):
    """Recurso não encontrado."""

    status_padrao = status.HTTP_404_NOT_FOUND
    codigo_padrao = "nao_encontrado"


class Conflict(ErroDominio):
    """Conflito de estado ou recurso duplicado."""

    status_padrao = status.HTTP_409_CONFLICT
    codigo_padrao = "conflito"


class Forbidden(ErroDominio):
    """Acesso negado para o usuário ou perfil atual."""

    status_padrao = status.HTTP_403_FORBIDDEN
    codigo_padrao = "acesso_negado"


class RuleViolation(ErroDominio):
    """Violação de regra de negócio."""

    status_padrao = status.HTTP_422_UNPROCESSABLE_CONTENT
    codigo_padrao = "violacao_regra"


def erro_http(status: int, codigo: str, mensagem: str, **extras: Any) -> HTTPException:
    """Cria uma `HTTPException` cujo `detail` traz `codigo`, `mensagem` e dados extras."""
    return HTTPException(
        status_code=status,
        detail={"codigo": codigo, "mensagem": mensagem, **extras},
    )


def _traduzir_mensagem_pydantic(erro: dict[str, Any]) -> str:
    tipo = str(erro.get("type", ""))
    ctx = erro.get("ctx") or {}
    msg_original = str(erro.get("msg", "Valor inválido."))

    if tipo == "missing":
        return "Campo obrigatório."
    if tipo == "string_type":
        return "Deve ser um texto."
    if tipo == "int_parsing" or tipo == "int_type":
        return "Deve ser um número inteiro válido."
    if tipo == "float_parsing" or tipo == "float_type":
        return "Deve ser um número válido."
    if tipo == "bool_parsing" or tipo == "bool_type":
        return "Deve ser verdadeiro ou falso."
    if tipo == "string_too_short" and "min_length" in ctx:
        return f"Deve ter pelo menos {ctx['min_length']} caracteres."
    if tipo == "string_too_long" and "max_length" in ctx:
        return f"Deve ter no máximo {ctx['max_length']} caracteres."
    if tipo == "greater_than" and "gt" in ctx:
        return f"Deve ser maior que {ctx['gt']}."
    if tipo == "greater_than_equal" and "ge" in ctx:
        return f"Deve ser maior ou igual a {ctx['ge']}."
    if tipo == "less_than" and "lt" in ctx:
        return f"Deve ser menor que {ctx['lt']}."
    if tipo == "less_than_equal" and "le" in ctx:
        return f"Deve ser menor ou igual a {ctx['le']}."
    if tipo == "value_error":
        return msg_original.replace("Value error, ", "", 1) if msg_original else "Valor inválido."
    if msg_original:
        return msg_original
    return "Valor inválido."


def _formatar_campo(loc: Any) -> str:
    if not isinstance(loc, (list, tuple)):
        return str(loc)
    partes = [str(parte) for parte in loc if parte not in ("body", "query", "path")]
    return ".".join(partes) if partes else "payload"


def _montar_corpo_erro(
    code: str,
    message: str,
    details: Any = None,
    detail_legado: Any = None,
) -> dict[str, Any]:
    corpo: dict[str, Any] = {
        "code": code,
        "message": message,
        "details": details,
    }
    if detail_legado is not None:
        corpo["detail"] = detail_legado
    return corpo


async def handler_erro_dominio(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ErroDominio)
    return JSONResponse(
        status_code=exc.status_code,
        content=_montar_corpo_erro(
            code=exc.code,
            message=exc.message,
            details=exc.details,
            detail_legado=exc.detail,
        ),
        headers=exc.headers,
    )


async def handler_http_exception(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, HTTPException)
    raw_detail = exc.detail
    if isinstance(raw_detail, dict):
        codigo = str(raw_detail.get("codigo") or raw_detail.get("code") or f"http_{exc.status_code}")
        mensagem = str(
            raw_detail.get("mensagem")
            or raw_detail.get("message")
            or raw_detail.get("detail")
            or "Erro na requisição."
        )
        extras = {
            k: v
            for k, v in raw_detail.items()
            if k not in ("codigo", "mensagem", "code", "message", "details")
        }
        details = extras if extras else None
        detail_legado = raw_detail
    else:
        codigo = f"http_{exc.status_code}"
        mensagem = str(raw_detail) if raw_detail else "Erro na requisição."
        details = None
        detail_legado = {"codigo": codigo, "mensagem": mensagem}

    return JSONResponse(
        status_code=exc.status_code,
        content=_montar_corpo_erro(
            code=codigo,
            message=mensagem,
            details=details,
            detail_legado=detail_legado,
        ),
        headers=exc.headers,
    )


async def handler_validacao_pydantic(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    itens: list[dict[str, str]] = []
    for erro in exc.errors():
        campo = _formatar_campo(erro.get("loc"))
        mensagem_pt = _traduzir_mensagem_pydantic(erro)
        itens.append({"field": campo, "message": mensagem_pt})

    primeira_msg = itens[0]["message"] if itens else "Dados de entrada inválidos."
    mensagem_geral = (
        f"Erro de validação: {itens[0]['field']} - {primeira_msg}"
        if itens
        else "Dados de entrada inválidos."
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=_montar_corpo_erro(
            code="erro_validacao",
            message=mensagem_geral,
            details={"errors": itens},
            detail_legado={"codigo": "erro_validacao", "mensagem": mensagem_geral, "erros": itens},
        ),
    )


async def handler_erro_inesperado(_request: Request, _exc: Exception) -> JSONResponse:
    mensagem = "Ocorreu um erro interno inesperado."
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_montar_corpo_erro(
            code="erro_interno",
            message=mensagem,
            details=None,
            detail_legado={"codigo": "erro_interno", "mensagem": mensagem},
        ),
    )
