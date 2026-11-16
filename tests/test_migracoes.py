from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from alembic import command
from app.config import settings
from app.db import Base


def _config() -> Config:
    return Config("alembic.ini")


def test_migracoes_criam_todas_as_tabelas_dos_modelos(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'migracao.db'}"
    monkeypatch.setattr(settings, "database_url", url)

    command.upgrade(_config(), "head")

    motor = create_engine(url)
    tabelas_banco = set(inspect(motor).get_table_names())
    tabelas_modelo = set(Base.metadata.tables)
    assert tabelas_modelo <= tabelas_banco


def test_migracoes_estao_em_dia_com_os_modelos(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'diff.db'}"
    monkeypatch.setattr(settings, "database_url", url)

    command.upgrade(_config(), "head")

    motor = create_engine(url)
    with motor.connect() as conexao:
        contexto = MigrationContext.configure(conexao, opts={"render_as_batch": True})
        diferencas = compare_metadata(contexto, Base.metadata)
    assert diferencas == []


def test_migracoes_podem_ser_revertidas(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'reversao.db'}"
    monkeypatch.setattr(settings, "database_url", url)

    command.upgrade(_config(), "head")
    command.downgrade(_config(), "base")

    motor = create_engine(url)
    assert set(inspect(motor).get_table_names()) <= {"alembic_version"}
