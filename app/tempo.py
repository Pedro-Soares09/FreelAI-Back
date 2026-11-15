"""Relógio da aplicação.

Todo código que depende da hora atual deve chamar `agora()`. Nos testes, o relógio pode ser
substituído para simular a passagem de horas ou dias sem esperar.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

_relogio: Callable[[], datetime] | None = None


def agora() -> datetime:
    """Retorna o instante atual em UTC, ou o instante fixado pelo relógio substituto."""
    if _relogio is not None:
        return _relogio()
    return datetime.now(UTC)


def fixar_relogio(instante: datetime) -> None:
    """Congela o relógio em `instante` (usado em testes)."""
    global _relogio
    _relogio = lambda: instante  # noqa: E731


def avancar_relogio(delta: timedelta) -> None:
    """Avança o relógio substituto; se não houver um, congela no instante atual."""
    fixar_relogio(agora() + delta)


def restaurar_relogio() -> None:
    """Volta a usar o relógio real."""
    global _relogio
    _relogio = None
