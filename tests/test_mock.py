import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize(
    ("path", "expected_key"),
    [
        ("/jobs", "titulo"),
        ("/conversations", "participantes"),
        ("/payments", "valor"),
    ],
)
def test_mock_endpoints_return_json_arrays(client, path, expected_key):
    response = client.get(path)

    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert expected_key in response.json()[0]
