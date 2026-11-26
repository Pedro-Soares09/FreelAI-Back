"""Limite de tentativas nas rotas de credencial, contado em memória por IP e por e-mail.

O contador é por processo: protege contra tentativas em sequência em uma instância, sem exigir
dependência nova. Os valores vêm de `configuracao` (tentativas e janela em segundos).
"""

import threading
import time
from collections import defaultdict, deque

from fastapi import Request, status
from sqlalchemy.orm import Session

from app.config import settings
from app.erros import erro_http
from app.services import configuracao

_tentativas: dict[str, deque[float]] = defaultdict(deque)
_trava = threading.Lock()


def limpar() -> None:
    """Zera os contadores (usado nos testes)."""
    with _trava:
        _tentativas.clear()


def _ip(request: Request) -> str:
    encaminhado = request.headers.get("x-forwarded-for", "")
    if encaminhado:
        return encaminhado.split(",")[0].strip()
    return request.client.host if request.client else "desconhecido"


def registrar_tentativa(db: Session, request: Request, email: str | None = None) -> None:
    """Conta a tentativa e recusa com 429 quando o IP ou o e-mail passou do limite na janela."""
    if not settings.limite_credenciais_ativo:
        return
    limite = int(configuracao.get("limite_credenciais_tentativas", db=db))
    janela = float(configuracao.get("limite_credenciais_janela_s", db=db))
    chaves = [f"ip:{_ip(request)}"]
    if email:
        chaves.append(f"email:{email.strip().lower()}")
    instante = time.monotonic()
    with _trava:
        for chave in chaves:
            fila = _tentativas[chave]
            while fila and instante - fila[0] > janela:
                fila.popleft()
            if len(fila) >= limite:
                raise erro_http(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    "muitas_tentativas",
                    "Muitas tentativas seguidas. Aguarde um instante e tente de novo.",
                    espere_segundos=int(janela),
                )
        for chave in chaves:
            _tentativas[chave].append(instante)
