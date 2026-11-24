"""Rotas provisórias que devolvem dados de exemplo.

Permitem que o front desenvolva as telas contra o contrato definitivo antes de as rotas reais
existirem. Cada rota é trocada pela implementação real mantendo o mesmo schema. Todas respondem
com o cabeçalho `X-Stub: true` e não exigem autenticação.
"""

from typing import Literal

from fastapi import APIRouter, Query, Response

from app.api.v1.schemas.integracao import (
    PontoMapa,
)

router = APIRouter()


def _marcar_stub(resposta: Response) -> None:
    resposta.headers["X-Stub"] = "true"


def _exemplo(modelo) -> dict:
    return modelo.model_config["json_schema_extra"]["examples"][0]


@router.get("/mapa/pontos", response_model=list[PontoMapa], tags=["mapa"])
def listar_pontos_do_mapa(
    resposta: Response,
    tipo: Literal["vaga", "profissional"] = Query(default="vaga"),
    latitude: float = Query(default=-8.0476, description="Latitude do centro da busca."),
    longitude: float = Query(default=-34.877, description="Longitude do centro da busca."),
    raio_km: int = Query(default=5, ge=1, le=15, description="Raio da busca, de 1 a 15 km."),
) -> list[dict]:
    """Pontos para o mapa, de vagas ou de profissionais (resposta de exemplo)."""
    _marcar_stub(resposta)
    base = _exemplo(PontoMapa)
    return [
        {**base, "id": 1, "tipo": tipo},
        {
            **base,
            "id": 2,
            "tipo": tipo,
            "titulo": "Diária de limpeza",
            "categoria": "diarista",
            "bairro": "Boa Viagem",
            "latitude": -8.1195,
            "longitude": -34.9005,
            "distancia_km": min(float(raio_km), 4.3),
            "valor": "120.00",
        },
    ]
