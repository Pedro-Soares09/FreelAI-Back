"""Coordenadas de bairros do Recife: tabela offline e geocodificação opcional pelo OpenStreetMap.

A tabela abaixo guarda o ponto central aproximado de cada bairro e serve de resposta imediata e
sem rede para cadastros e vagas sem coordenadas. Quando `GEOCODIFICACAO_OSM=true`, bairros fora
da tabela são procurados no Nominatim (OpenStreetMap), enviando apenas o nome do bairro e a
cidade, nunca dados da pessoa. Qualquer falha de rede cai no resultado vazio.
"""

import json
import logging
import unicodedata
import urllib.parse
import urllib.request

from fastapi import status

from app.config import settings
from app.erros import erro_http

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "freelai-geocodificador/1.0"
TIMEOUT_S = 3.0

# Ponto central aproximado de cada bairro: (nome, latitude, longitude).
BAIRROS_RECIFE: tuple[tuple[str, float, float], ...] = (
    ("Afogados", -8.0775, -34.9097),
    ("Aflitos", -8.0390, -34.8970),
    ("Água Fria", -8.0152, -34.8928),
    ("Arruda", -8.0225, -34.8925),
    ("Beberibe", -8.0050, -34.8990),
    ("Boa Viagem", -8.1190, -34.9040),
    ("Boa Vista", -8.0630, -34.8760),
    ("Brasília Teimosa", -8.0870, -34.8760),
    ("Cajueiro", -8.0300, -34.9100),
    ("Campo Grande", -8.0285, -34.8830),
    ("Casa Amarela", -8.0260, -34.9200),
    ("Casa Forte", -8.0360, -34.9180),
    ("Cordeiro", -8.0540, -34.9230),
    ("Derby", -8.0540, -34.8990),
    ("Encruzilhada", -8.0330, -34.8930),
    ("Espinheiro", -8.0400, -34.8960),
    ("Graças", -8.0420, -34.8980),
    ("Hipódromo", -8.0390, -34.8860),
    ("Ibura", -8.1170, -34.9440),
    ("Imbiribeira", -8.1120, -34.9170),
    ("Ipsep", -8.1170, -34.9370),
    ("Jaqueira", -8.0390, -34.9060),
    ("Madalena", -8.0520, -34.9010),
    ("Mustardinha", -8.0680, -34.9150),
    ("Parnamirim", -8.0350, -34.9110),
    ("Pina", -8.0900, -34.8900),
    ("Poço da Panela", -8.0300, -34.9130),
    ("Prado", -8.0620, -34.9120),
    ("Recife Antigo", -8.0630, -34.8710),
    ("Rosarinho", -8.0300, -34.8970),
    ("Santo Amaro", -8.0540, -34.8800),
    ("São José", -8.0680, -34.8780),
    ("Setúbal", -8.1330, -34.9100),
    ("Soledade", -8.0510, -34.8890),
    ("Tamarineira", -8.0300, -34.8970),
    ("Torre", -8.0480, -34.9120),
    ("Torreão", -8.0400, -34.8800),
    ("Várzea", -8.0500, -34.9500),
    ("Zumbi", -8.0780, -34.9160),
)


def _normalizar(nome: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.lower().split())


_POR_NOME = {_normalizar(nome): (lat, lon) for nome, lat, lon in BAIRROS_RECIFE}


def coordenadas_offline(bairro: str) -> tuple[float, float] | None:
    """Ponto central do bairro na tabela embutida, sem diferenciar acentos e maiúsculas."""
    return _POR_NOME.get(_normalizar(bairro))


def geocodificar_osm(bairro: str, cidade: str = "Recife") -> tuple[float, float] | None:
    """Procura o bairro no Nominatim (OpenStreetMap). Devolve `None` em qualquer falha."""
    consulta = urllib.parse.urlencode(
        {"q": f"{bairro}, {cidade}, PE, Brasil", "format": "json", "limit": 1}
    )
    pedido = urllib.request.Request(  # noqa: S310 - URL fixa, https
        f"{NOMINATIM_URL}?{consulta}", headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(pedido, timeout=TIMEOUT_S) as resposta:  # noqa: S310
            achados = json.loads(resposta.read().decode("utf-8"))
        if achados:
            return float(achados[0]["lat"]), float(achados[0]["lon"])
    except (OSError, ValueError, KeyError, IndexError):
        logger.info("Geocodificação OSM indisponível para o bairro informado.")
    return None


def resolver_coordenadas(
    bairro: str, latitude: float | None, longitude: float | None
) -> tuple[float, float]:
    """Usa as coordenadas informadas ou deduz do bairro (tabela offline, depois OpenStreetMap)."""
    if latitude is not None and longitude is not None:
        return latitude, longitude
    achado = coordenadas_offline(bairro)
    if achado is None and settings.geocodificacao_osm:
        achado = geocodificar_osm(bairro)
    if achado is None:
        raise erro_http(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "coordenadas_nao_encontradas",
            "Informe latitude e longitude: não foi possível localizar o bairro.",
        )
    return achado
