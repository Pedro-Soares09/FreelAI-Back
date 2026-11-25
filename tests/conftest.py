import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db import Base, get_db
from app.main import app
from app.tempo import restaurar_relogio


@pytest.fixture
def engine():
    motor = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=motor)
    yield motor
    Base.metadata.drop_all(bind=motor)
    motor.dispose()


@pytest.fixture
def db(engine) -> Session:
    sessao = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        yield sessao
    finally:
        sessao.close()


@pytest.fixture
def cliente(engine):
    """Cliente HTTP da API completa, ligado ao banco de teste em memória."""
    fabrica = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def sobrescrever_get_db():
        sessao = fabrica()
        try:
            yield sessao
        finally:
            sessao.close()

    app.dependency_overrides[get_db] = sobrescrever_get_db
    with TestClient(app) as cliente_http:
        yield cliente_http
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True, scope="session")
def custo_de_hash_reduzido():
    """Reduz o custo do bcrypt só na suíte: cada conta criada nos testes gerava um hash lento."""
    from app.security import password_context

    password_context.update(bcrypt__rounds=4)


@pytest.fixture(autouse=True)
def varreduras_automaticas_desligadas(monkeypatch):
    """O agendador não roda nos testes: as varreduras são chamadas explicitamente."""
    from app.config import settings

    monkeypatch.setattr(settings, "varreduras_automaticas", False)


@pytest.fixture(autouse=True)
def pasta_uploads_isolada(tmp_path, monkeypatch):
    """Direciona os uploads de cada teste para um diretório temporário descartado ao fim."""
    from app.config import settings

    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path / "uploads"))


@pytest.fixture(autouse=True)
def relogio_real():
    """Garante que nenhum teste vaze um relógio fixado ou plano Pro em memória."""
    from app.services.notificacoes import limpar_planos_pro

    limpar_planos_pro()
    yield
    limpar_planos_pro()
    restaurar_relogio()
