"""Classes de equivalência e valores limite do registro e do login (via API).

Complementa test_auth_v1.py sem repetir o que ele já cobre.
"""

import pytest

from app.security import create_access_token
from tests.fabricas import SENHA_PADRAO, criar_usuario

BASE = "/api/v1/auth"


def _registro(cliente, **campos):
    corpo = {"nome": "Ana", "email": "limite@example.com", "senha": SENHA_PADRAO, **campos}
    return cliente.post(f"{BASE}/registro", json=corpo)


@pytest.mark.parametrize(
    ("tamanho", "status"),
    [(7, 422), (8, 201), (72, 201), (73, 422)],
    ids=["7-recusa", "8-aceita", "72-aceita", "73-recusa"],
)
def test_senha_nos_limites_de_8_e_72_caracteres(cliente, tamanho, status):
    """CT-REG-02: senha com 7, 8, 72 e 73 caracteres ASCII."""
    resposta = _registro(cliente, senha="a" * tamanho)

    assert resposta.status_code == status


@pytest.mark.parametrize(
    ("caracteres", "status"),
    [(36, 201), (37, 422)],
    ids=["72-bytes-aceita", "74-bytes-recusa"],
)
def test_senha_com_acentos_respeita_o_limite_de_72_bytes(cliente, caracteres, status):
    """CT-REG-02: 'ã' ocupa 2 bytes em UTF-8; 36 cabem em 72 bytes e 37 não."""
    resposta = _registro(cliente, senha="ã" * caracteres)

    assert resposta.status_code == status


@pytest.mark.parametrize(
    ("nome", "status"),
    [
        ("", 422),
        ("   ", 422),
        ("A", 201),
        ("a" * 120, 201),
        ("a" * 121, 422),
    ],
    ids=["vazio", "so-espacos", "1-caractere", "120-aceita", "121-recusa"],
)
def test_nome_nos_limites_de_1_e_120_caracteres(cliente, nome, status):
    """CT-REG-03: nome vazio, só espaços, 1, 120 e 121 caracteres."""
    resposta = _registro(cliente, nome=nome)

    assert resposta.status_code == status


@pytest.mark.parametrize(
    "email",
    ["sem-arroba", "sem-dominio@", "@sem-usuario.com", "dois@@example.com", "espaco @example.com"],
)
def test_email_invalido_e_recusado_no_registro(cliente, email):
    """CT-REG-04: e-mail fora do formato é recusado com 422."""
    resposta = _registro(cliente, email=email)

    assert resposta.status_code == 422


def test_login_com_email_inexistente_responde_igual_a_senha_errada(cliente):
    """CT-LOG-04: a resposta não revela se o e-mail existe."""
    _registro(cliente, email="existe@example.com")

    inexistente = cliente.post(
        f"{BASE}/login", json={"email": "nao-existe@example.com", "senha": SENHA_PADRAO}
    )
    senha_errada = cliente.post(
        f"{BASE}/login", json={"email": "existe@example.com", "senha": "errada-12345"}
    )

    assert inexistente.status_code == senha_errada.status_code == 401
    assert inexistente.json()["detail"] == senha_errada.json()["detail"]


def test_login_recusa_campos_vazios_no_corpo(cliente):
    """CT-LOG-03: a API também recusa e-mail ou senha ausentes ou vazios."""
    sem_senha = cliente.post(f"{BASE}/login", json={"email": "a@example.com", "senha": ""})
    sem_email = cliente.post(f"{BASE}/login", json={"senha": SENHA_PADRAO})

    assert sem_senha.status_code == 422
    assert sem_email.status_code == 422


def test_login_diferencia_maiusculas_so_na_senha(cliente):
    """CT-LOG-05: o e-mail não diferencia maiúsculas; a senha diferencia."""
    _registro(cliente, email="caso@example.com")

    email_em_caixa_alta = cliente.post(
        f"{BASE}/login", json={"email": "CASO@EXAMPLE.COM", "senha": SENHA_PADRAO}
    )
    senha_em_caixa_alta = cliente.post(
        f"{BASE}/login", json={"email": "caso@example.com", "senha": SENHA_PADRAO.upper()}
    )

    assert email_em_caixa_alta.status_code == 200
    assert senha_em_caixa_alta.status_code == 401


def test_token_adulterado_e_recusado(cliente, db):
    """CT-LOG-06: troca de um caractere da assinatura invalida o token."""
    usuario = criar_usuario(db)
    token = create_access_token(str(usuario.id))
    cabeca, corpo, assinatura = token.split(".")
    trocado = "A" if assinatura[0] != "A" else "B"
    adulterado = ".".join([cabeca, corpo, trocado + assinatura[1:]])

    resposta = cliente.get(f"{BASE}/me", headers={"Authorization": f"Bearer {adulterado}"})

    assert resposta.status_code == 401


def test_token_de_usuario_inexistente_e_recusado(cliente):
    """CT-LOG-06: token bem formado para um id que não existe é recusado."""
    token = create_access_token("999999")

    resposta = cliente.get(f"{BASE}/me", headers={"Authorization": f"Bearer {token}"})

    assert resposta.status_code == 401


def test_cabecalho_sem_esquema_bearer_e_recusado(cliente, db):
    """CT-LOG-06: o esquema de autorização precisa ser Bearer."""
    usuario = criar_usuario(db)
    token = create_access_token(str(usuario.id))

    resposta = cliente.get(f"{BASE}/me", headers={"Authorization": token})

    assert resposta.status_code in (401, 403)


def _excluir_conta(db, email):
    from app.models import Usuario
    from app.tempo import agora

    conta = db.query(Usuario).filter_by(email=email).one()
    conta.excluida_em = agora()
    db.commit()


def test_login_de_conta_excluida_com_senha_correta_e_barrado(cliente, db):
    """CT-LOG-07: conta excluída não recebe tokens; a regra devolve 403 conta_excluida."""
    _registro(cliente, email="excluida@example.com")
    _excluir_conta(db, "excluida@example.com")

    resposta = cliente.post(
        f"{BASE}/login", json={"email": "excluida@example.com", "senha": SENHA_PADRAO}
    )

    assert resposta.status_code == 403
    assert resposta.json()["detail"]["codigo"] == "conta_excluida"


def test_consulta_de_sancao_de_conta_excluida_responde_como_credencial_invalida(cliente, db):
    """CT-LOG-07: a consulta de sanção não confirma a existência de conta excluída."""
    _registro(cliente, email="excluida2@example.com")
    _excluir_conta(db, "excluida2@example.com")

    resposta = cliente.post(
        f"{BASE}/sancao", json={"email": "excluida2@example.com", "senha": SENHA_PADRAO}
    )

    assert resposta.status_code == 401
    assert resposta.json()["detail"]["codigo"] == "credenciais_invalidas"
