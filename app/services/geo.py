"""Cálculos geográficos sem extensão do banco: distância de Haversine e caixa de busca.

As coordenadas ficam em colunas `float` na tabela `enderecos`. O corte por raio é feito em duas
etapas: uma caixa de latitude e longitude filtra no SQL, e a distância exata é calculada em
Python somente para os registros que sobraram.
"""

import math
from dataclasses import dataclass

RAIO_TERRA_KM = 6371.0088


def distancia_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distância em km entre dois pontos pela fórmula de Haversine (função pura)."""
    fi1, fi2 = math.radians(lat1), math.radians(lat2)
    delta_fi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_fi / 2) ** 2
        + math.cos(fi1) * math.cos(fi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * RAIO_TERRA_KM * math.asin(math.sqrt(a))


def arredondar_km(distancia: float) -> float:
    """Distância com uma casa decimal, formato usado ao exibir para o usuário."""
    return round(distancia, 1)


@dataclass(frozen=True)
class CaixaGeografica:
    """Retângulo de latitude e longitude que envolve um círculo de busca."""

    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float

    def contem(self, latitude: float, longitude: float) -> bool:
        return (
            self.lat_min <= latitude <= self.lat_max and self.lon_min <= longitude <= self.lon_max
        )


def caixa_para_raio(latitude: float, longitude: float, raio_km: float) -> CaixaGeografica:
    """Caixa que contém todos os pontos a até `raio_km` do centro.

    Serve de pré-filtro: pontos fora da caixa estão com certeza fora do raio, mas pontos dentro
    dela ainda precisam passar pelo corte exato com `distancia_km`.
    """
    delta_lat = math.degrees(raio_km / RAIO_TERRA_KM)
    cos_lat = math.cos(math.radians(latitude))
    delta_lon = 180.0 if cos_lat < 1e-9 else math.degrees(raio_km / (RAIO_TERRA_KM * cos_lat))
    lon_min, lon_max = longitude - delta_lon, longitude + delta_lon
    # Perto dos polos ou do antimeridiano a faixa de longitude daria a volta no globo; a caixa
    # abre para a volta inteira em vez de excluir pontos que estão dentro do raio.
    if lon_min < -180.0 or lon_max > 180.0:
        lon_min, lon_max = -180.0, 180.0
    return CaixaGeografica(
        lat_min=max(-90.0, latitude - delta_lat),
        lat_max=min(90.0, latitude + delta_lat),
        lon_min=lon_min,
        lon_max=lon_max,
    )
