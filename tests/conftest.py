import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db import Base
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


@pytest.fixture(autouse=True)
def relogio_real():
    """Garante que nenhum teste vaze um relógio fixado para o seguinte."""
    yield
    restaurar_relogio()
