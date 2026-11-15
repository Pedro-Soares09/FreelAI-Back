from fastapi.testclient import TestClient

from app.main import app


def test_health_v1_informa_status_e_perfil():
    with TestClient(app) as client:
        resposta = client.get("/api/v1/health")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["status"] == "ok"
    assert corpo["perfil"] in {"producao", "demo"}


def test_documentacao_interativa_disponivel():
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200
