import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

RAIZ = Path(__file__).resolve().parent.parent


def _carregar_exportador():
    especificacao = importlib.util.spec_from_file_location(
        "exportar_openapi", RAIZ / "scripts" / "export_openapi.py"
    )
    modulo = importlib.util.module_from_spec(especificacao)
    especificacao.loader.exec_module(modulo)
    return modulo


def test_contrato_versionado_esta_em_dia():
    exportador = _carregar_exportador()

    atual = (RAIZ / "contracts" / "openapi.json").read_text(encoding="utf-8")

    assert atual == exportador.gerar(), (
        "contracts/openapi.json desatualizado. Rode: python scripts/export_openapi.py"
    )


@pytest.mark.parametrize(
    "schema",
    ["UsuarioAtual", "ConversaResumo", "DadosAcordo", "PontoMapa", "ArquivoEnviado"],
)
def test_pontos_de_integracao_tem_schema_com_exemplo(schema):
    componentes = app.openapi()["components"]["schemas"]

    assert schema in componentes
    assert componentes[schema]["examples"], f"{schema} sem exemplo"


def test_rotas_anteriores_ficam_fora_do_contrato():
    caminhos = app.openapi()["paths"]

    assert "/register" not in caminhos
    assert "/jobs" not in caminhos
    assert all(caminho.startswith("/api/v1") for caminho in caminhos)


def test_operacoes_tem_identificadores_unicos():
    operacoes = [
        operacao["operationId"]
        for caminho in app.openapi()["paths"].values()
        for operacao in caminho.values()
    ]

    assert len(operacoes) == len(set(operacoes))


def test_stubs_respondem_com_o_exemplo_e_o_cabecalho():
    cliente = TestClient(app)

    mapa = cliente.get("/api/v1/mapa/pontos?tipo=profissional&raio_km=10")

    for resposta in (mapa,):
        assert resposta.status_code == 200
        assert resposta.headers["x-stub"] == "true"
    assert {ponto["tipo"] for ponto in mapa.json()} == {"profissional"}


def test_mapa_recusa_raio_acima_do_limite():
    resposta = TestClient(app).get("/api/v1/mapa/pontos?raio_km=50")

    assert resposta.status_code == 422
