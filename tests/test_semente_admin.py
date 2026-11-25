import sys
from pathlib import Path

from sqlalchemy import select

from app.models import PapelUsuario, Usuario
from app.security import verify_password
from app.seed import SENHA_SEED, semear

EMAIL_ADMIN = "admin@freelai.local"


def _admin(db) -> Usuario:
    return db.scalar(select(Usuario).where(Usuario.email == EMAIL_ADMIN))


def test_sem_a_variavel_o_admin_nao_nasce_com_senha_conhecida(db, monkeypatch):
    monkeypatch.delenv("SENHA_ADMIN_DEMO", raising=False)

    semear(db)

    admin = _admin(db)
    assert admin.papel == PapelUsuario.ADMIN
    for conhecida in (SENHA_SEED, "senha-segura-123", "admin", "123456"):
        assert not verify_password(conhecida, admin.senha_hash)


def test_com_a_variavel_o_admin_usa_a_senha_informada(db, monkeypatch):
    monkeypatch.setenv("SENHA_ADMIN_DEMO", "valor-so-do-deploy-123")

    semear(db)

    assert verify_password("valor-so-do-deploy-123", _admin(db).senha_hash)


def test_rodar_a_semente_de_novo_nao_redefine_senhas_existentes(db, monkeypatch):
    monkeypatch.setenv("SENHA_ADMIN_DEMO", "primeira-senha-do-admin")
    semear(db)
    profissional = db.scalar(select(Usuario).where(Usuario.email == "profissional01@freelai.local"))
    profissional.senha_hash = "hash-trocado-pelo-dono"
    db.commit()

    monkeypatch.setenv("SENHA_ADMIN_DEMO", "outra-senha-depois")
    semear(db)

    assert verify_password("primeira-senha-do-admin", _admin(db).senha_hash)
    db.refresh(profissional)
    assert profissional.senha_hash == "hash-trocado-pelo-dono"


def test_so_o_admin_da_semente_tem_papel_administrativo(db, monkeypatch):
    monkeypatch.delenv("SENHA_ADMIN_DEMO", raising=False)

    semear(db)

    administradores = db.scalars(
        select(Usuario.email).where(Usuario.papel == PapelUsuario.ADMIN)
    ).all()
    assert administradores == [EMAIL_ADMIN]


def test_admin_da_semente_curta_tambem_depende_da_variavel(db, monkeypatch):
    scripts = str(Path(__file__).resolve().parents[1] / "scripts")
    monkeypatch.syspath_prepend(scripts)
    sys.modules.pop("semear_demo", None)
    import semear_demo

    monkeypatch.delenv("SENHA_ADMIN_DEMO", raising=False)
    dados = next(c for c in semear_demo.CONTAS if c.get("admin"))
    admin = semear_demo._obter_ou_criar(db, dados)
    db.commit()
    assert not verify_password(semear_demo.SENHA_DEMO, admin.senha_hash)

    comum = next(c for c in semear_demo.CONTAS if not c.get("admin"))
    ana = semear_demo._obter_ou_criar(db, comum)
    assert verify_password(semear_demo.SENHA_DEMO, ana.senha_hash)
