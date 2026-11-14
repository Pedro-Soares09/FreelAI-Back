import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app

test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture
def client():
    Base.metadata.create_all(bind=test_engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=test_engine)


def test_register_login_and_get_me(client):
    registration = client.post(
        "/register",
        json={
            "nome": "Ana Freelancer",
            "email": "ana@example.com",
            "senha": "senha-segura-123",
            "tipo": "freelancer",
        },
    )

    assert registration.status_code == 201
    assert registration.json()["email"] == "ana@example.com"
    assert "senha" not in registration.json()

    login = client.post(
        "/login",
        json={"email": "ana@example.com", "senha": "senha-segura-123"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    current_user = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert current_user.status_code == 200
    assert current_user.json()["nome"] == "Ana Freelancer"


def test_register_rejects_duplicate_email(client):
    payload = {
        "nome": "Ana Freelancer",
        "email": "ana@example.com",
        "senha": "senha-segura-123",
    }
    assert client.post("/register", json=payload).status_code == 201
    assert client.post("/register", json=payload).status_code == 409


def test_login_rejects_wrong_password(client):
    client.post(
        "/register",
        json={
            "nome": "Ana Freelancer",
            "email": "ana@example.com",
            "senha": "senha-segura-123",
        },
    )
    response = client.post(
        "/login",
        json={"email": "ana@example.com", "senha": "senha-incorreta"},
    )

    assert response.status_code == 401


def test_me_requires_valid_token(client):
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers={"Authorization": "Bearer token-invalido"}).status_code == 401
