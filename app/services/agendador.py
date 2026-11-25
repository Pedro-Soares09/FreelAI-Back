"""Agendador das varreduras: roda periodicamente o mesmo conjunto da rota administrativa.

O laço executa o trabalho síncrono (sessão do banco) em uma thread, fora do event loop, e nunca
é derrubado por uma falha: o erro é registrado e a próxima rodada acontece no intervalo normal.
"""

import asyncio
import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.services import varreduras

logger = logging.getLogger("freelai.varreduras")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s freelai.varreduras: %(message)s"))
    logger.addHandler(_handler)
logger.setLevel(logging.INFO)
logger.propagate = False

INTERVALO_DEMO_S = 30
INTERVALO_PRODUCAO_S = 60


def intervalo_em_segundos() -> float:
    """Intervalo configurado, ou o padrão do perfil quando `varreduras_intervalo_s` é 0."""
    if settings.varreduras_intervalo_s > 0:
        return float(settings.varreduras_intervalo_s)
    return float(INTERVALO_DEMO_S if settings.perfil == "demo" else INTERVALO_PRODUCAO_S)


def rodar_uma_vez(fabrica_sessao: Callable[[], Session] = SessionLocal) -> dict:
    """Uma rodada completa em sessão própria, a mesma lógica de `POST /admin/prazos/varrer`."""
    with fabrica_sessao() as db:
        return varreduras.executar_varreduras(db)


async def laco(
    intervalo_s: float | None = None,
    fabrica_sessao: Callable[[], Session] = SessionLocal,
) -> None:
    """Executa as varreduras a cada intervalo até ser cancelado no encerramento do app."""
    espera = intervalo_s if intervalo_s is not None else intervalo_em_segundos()
    logger.info("Varreduras automáticas ligadas (a cada %s s).", espera)
    while True:
        await asyncio.sleep(espera)
        try:
            resultado = await asyncio.to_thread(rodar_uma_vez, fabrica_sessao)
            alteradas = {k: v for k, v in resultado["alteracoes"].items() if v}
            if alteradas:
                logger.info("Varreduras aplicadas: %s", alteradas)
        except Exception:
            logger.exception("Falha em uma rodada de varreduras; a próxima segue no prazo.")
