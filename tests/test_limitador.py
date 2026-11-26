"""Limite de tentativas nas rotas de credencial (login, registro, sanção e contestação)."""

import pytest

from app.config import settings
from app.services import configuracao, limitador


@pytest.fixture
def limite_ligado(monkeypatch):
    monkeypatch.setattr(settings, "limite_credenciais_ativo", True)
    limitador.limpar()
    yield
    limitador.limpar()


def _login(cliente, email, senha="qualquer-senha-123"):
    return cliente.post("/api/v1/auth/login", json={"email": email, "senha": senha})


def test_login_e_limitado_por_email_na_janela(cliente, limite_ligado):
    """CT-LOG-08: depois de 10 tentativas no mesmo e-mail, a 11ª recebe 429."""
    respostas = [_login(cliente, "alvo@example.com") for _ in range(11)]

    assert [r.status_code for r in respostas[:10]] == [401] * 10
    assert respostas[10].status_code == 429
    assert respostas[10].json()["detail"]["codigo"] == "muitas_tentativas"


def test_limite_por_ip_vale_para_emails_diferentes(cliente, limite_ligado):
    """CT-LOG-08: o mesmo IP tentando e-mails variados também é limitado."""
    respostas = [_login(cliente, f"pessoa{n}@example.com") for n in range(11)]

    assert respostas[10].status_code == 429


def test_limite_cobre_registro_sancao_e_contestacao(cliente, limite_ligado, monkeypatch):
    """CT-LOG-08: as rotas de credencial compartilham o limite por IP."""
    monkeypatch.setitem(
        configuracao.PARAMETROS,
        "limite_credenciais_tentativas",
        configuracao.Parametro(2, 2, "teste"),
    )
    cliente.post(
        "/api/v1/auth/registro",
        json={"nome": "Ana", "email": "a@example.com", "senha": "senha-segura-123"},
    )
    cliente.post("/api/v1/auth/sancao", json={"email": "a@example.com", "senha": "x"})

    contestacao = cliente.post(
        "/api/v1/auth/contestacao",
        json={"email": "a@example.com", "senha": "x", "texto": "Peço revisão da sanção."},
    )

    assert contestacao.status_code == 429


def test_janela_vencida_libera_novas_tentativas(cliente, limite_ligado, monkeypatch):
    """CT-LOG-08: passada a janela, o contador volta a aceitar tentativas."""
    monkeypatch.setitem(
        configuracao.PARAMETROS,
        "limite_credenciais_tentativas",
        configuracao.Parametro(1, 1, "teste"),
    )
    instante = {"t": 1000.0}
    monkeypatch.setattr(limitador.time, "monotonic", lambda: instante["t"])
    _login(cliente, "janela@example.com")
    assert _login(cliente, "janela@example.com").status_code == 429

    instante["t"] += 61

    assert _login(cliente, "janela@example.com").status_code == 401


def test_limite_desligado_nao_bloqueia(cliente):
    """O padrão dos testes mantém o limite desligado."""
    respostas = [_login(cliente, "livre@example.com") for _ in range(15)]

    assert all(r.status_code == 401 for r in respostas)
