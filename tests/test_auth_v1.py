from datetime import timedelta

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.db import get_db
from app.deps import exige_admin, exige_conta_ativa, exige_perfil, usuario_atual
from app.models import EstadoConta, PapelUsuario, TipoPerfil
from app.security import create_access_token, create_refresh_token
from app.services.autenticacao import exigir_nao_autocontratacao
from app.tempo import agora
from tests.fabricas import SENHA_PADRAO, cabecalho, criar_usuario

BASE = "/api/v1/auth"


def _registrar(cliente, email="ana@example.com"):
    return cliente.post(
        f"{BASE}/registro", json={"nome": "Ana", "email": email, "senha": SENHA_PADRAO}
    )


def test_registro_cria_conta_pendente_sem_perfis(cliente):
    resposta = _registrar(cliente)

    assert resposta.status_code == 201
    corpo = resposta.json()
    assert corpo["estado"] == "pendente"
    assert corpo["perfis"] == []
    assert corpo["perfil_ativo"] is None
    assert "senha" not in corpo and "senha_hash" not in corpo


def test_registro_rejeita_email_repetido_sem_diferenciar_maiusculas(cliente):
    assert _registrar(cliente, "ana@example.com").status_code == 201

    resposta = _registrar(cliente, "ANA@example.com")

    assert resposta.status_code == 409
    assert resposta.json()["detail"]["codigo"] == "email_em_uso"


def test_registro_rejeita_senha_curta(cliente):
    resposta = cliente.post(
        f"{BASE}/registro", json={"nome": "Ana", "email": "a@example.com", "senha": "curta"}
    )
    assert resposta.status_code == 422


def test_login_devolve_par_de_tokens_e_me_responde(cliente):
    _registrar(cliente)

    login = cliente.post(f"{BASE}/login", json={"email": "ana@example.com", "senha": SENHA_PADRAO})

    assert login.status_code == 200
    tokens = login.json()
    assert tokens["token_type"] == "bearer"
    assert tokens["expira_em_segundos"] > 0
    me = cliente.get(f"{BASE}/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == "ana@example.com"


def test_login_rejeita_senha_incorreta(cliente):
    _registrar(cliente)

    resposta = cliente.post(
        f"{BASE}/login", json={"email": "ana@example.com", "senha": "errada-123"}
    )

    assert resposta.status_code == 401
    assert resposta.json()["detail"]["codigo"] == "credenciais_invalidas"


def test_me_exige_token_valido(cliente):
    assert cliente.get(f"{BASE}/me").status_code == 401
    invalido = cliente.get(f"{BASE}/me", headers={"Authorization": "Bearer abc"})
    assert invalido.status_code == 401


def test_refresh_gera_novo_par_e_recusa_token_de_acesso(cliente, db):
    usuario = criar_usuario(db)

    valido = cliente.post(
        f"{BASE}/refresh", json={"refresh_token": create_refresh_token(str(usuario.id))}
    )
    errado = cliente.post(
        f"{BASE}/refresh", json={"refresh_token": create_access_token(str(usuario.id))}
    )

    assert valido.status_code == 200
    assert errado.status_code == 401


def test_token_de_renovacao_nao_serve_como_token_de_acesso(cliente, db):
    usuario = criar_usuario(db)

    resposta = cliente.get(
        f"{BASE}/me",
        headers={"Authorization": f"Bearer {create_refresh_token(str(usuario.id))}"},
    )

    assert resposta.status_code == 401


def test_token_expirado_e_recusado(cliente, db):
    usuario = criar_usuario(db)
    expirado = create_access_token(str(usuario.id), duracao=timedelta(seconds=-5))

    resposta = cliente.get(f"{BASE}/me", headers={"Authorization": f"Bearer {expirado}"})

    assert resposta.status_code == 401


def test_usuario_com_dois_perfis_alterna_e_api_devolve_perfil_ativo(cliente, db):
    usuario = criar_usuario(
        db,
        perfis=(TipoPerfil.PROFISSIONAL, TipoPerfil.CONTRATANTE),
        perfil_ativo=TipoPerfil.PROFISSIONAL,
    )

    resposta = cliente.put(
        f"{BASE}/perfil-ativo", json={"perfil": "contratante"}, headers=cabecalho(usuario)
    )

    assert resposta.status_code == 200
    assert resposta.json()["perfil_ativo"] == "contratante"
    assert set(resposta.json()["perfis"]) == {"profissional", "contratante"}
    assert (
        cliente.get(f"{BASE}/me", headers=cabecalho(usuario)).json()["perfil_ativo"]
        == "contratante"
    )


def test_nao_alterna_para_perfil_nao_cadastrado(cliente, db):
    usuario = criar_usuario(db, perfis=(TipoPerfil.PROFISSIONAL,))

    resposta = cliente.put(
        f"{BASE}/perfil-ativo", json={"perfil": "contratante"}, headers=cabecalho(usuario)
    )

    assert resposta.status_code == 409
    assert resposta.json()["detail"]["codigo"] == "perfil_nao_cadastrado"


def test_me_indica_verificacao_do_perfil_ativo(cliente, db):
    usuario = criar_usuario(
        db, perfis=(TipoPerfil.PROFISSIONAL,), perfil_ativo=TipoPerfil.PROFISSIONAL, verificado=True
    )

    assert cliente.get(f"{BASE}/me", headers=cabecalho(usuario)).json()["verificado"] is True


def test_contratante_nao_pode_escolher_o_proprio_perfil_profissional(db):
    usuario = criar_usuario(db, perfis=(TipoPerfil.PROFISSIONAL, TipoPerfil.CONTRATANTE))

    try:
        exigir_nao_autocontratacao(usuario.perfil_contratante, usuario.perfil_profissional)
    except Exception as erro:  # noqa: BLE001
        assert erro.status_code == 409
        assert erro.detail["codigo"] == "autocontratacao"
    else:
        raise AssertionError("A autocontratação deveria ser recusada")


def test_contratante_pode_escolher_profissional_de_outra_conta(db):
    contratante = criar_usuario(db, perfis=(TipoPerfil.CONTRATANTE,))
    profissional = criar_usuario(db, perfis=(TipoPerfil.PROFISSIONAL,))

    exigir_nao_autocontratacao(contratante.perfil_contratante, profissional.perfil_profissional)


def test_conta_suspensa_e_barrada_com_data_de_retorno(cliente, db):
    retorno = agora() + timedelta(days=7)
    usuario = criar_usuario(db, estado=EstadoConta.SUSPENSA)
    usuario.suspensa_ate = retorno
    db.commit()

    resposta = cliente.get(f"{BASE}/me", headers=cabecalho(usuario))

    assert resposta.status_code == 403
    assert resposta.json()["detail"]["codigo"] == "conta_suspensa"
    assert resposta.json()["detail"]["retorno_em"] is not None


def test_login_de_conta_suspensa_informa_estado(cliente, db):
    usuario = criar_usuario(db, estado=EstadoConta.SUSPENSA, email="s@example.com")
    usuario.suspensa_ate = agora() + timedelta(days=30)
    db.commit()

    resposta = cliente.post(f"{BASE}/login", json={"email": "s@example.com", "senha": SENHA_PADRAO})

    assert resposta.status_code == 403
    assert resposta.json()["detail"]["codigo"] == "conta_suspensa"


def test_suspensao_vencida_reativa_a_conta(cliente, db):
    usuario = criar_usuario(db, estado=EstadoConta.SUSPENSA)
    usuario.suspensa_ate = agora() - timedelta(minutes=1)
    db.commit()

    resposta = cliente.get(f"{BASE}/me", headers=cabecalho(usuario))

    assert resposta.status_code == 200
    assert resposta.json()["estado"] == "ativa"


def test_conta_banida_nao_entra(cliente, db):
    usuario = criar_usuario(db, estado=EstadoConta.BANIDA)

    resposta = cliente.get(f"{BASE}/me", headers=cabecalho(usuario))

    assert resposta.status_code == 403
    assert resposta.json()["detail"]["codigo"] == "conta_banida"


def _app_de_protecao(engine_db) -> FastAPI:
    """Aplicação mínima para exercitar as dependências de autorização."""
    pequena = FastAPI()

    @pequena.get("/ativa")
    def rota_ativa(usuario=Depends(exige_conta_ativa)):
        return {"id": usuario.id}

    @pequena.get("/profissional")
    def rota_profissional(usuario=Depends(exige_perfil(TipoPerfil.PROFISSIONAL))):
        return {"id": usuario.id}

    @pequena.get("/admin")
    def rota_admin(usuario=Depends(exige_admin)):
        return {"id": usuario.id}

    @pequena.get("/qualquer")
    def rota_qualquer(usuario=Depends(usuario_atual)):
        return {"id": usuario.id}

    pequena.dependency_overrides[get_db] = engine_db
    return pequena


def test_dependencias_de_autorizacao(engine, db):
    from sqlalchemy.orm import sessionmaker

    fabrica = sessionmaker(bind=engine)

    def sessao():
        s = fabrica()
        try:
            yield s
        finally:
            s.close()

    cliente = TestClient(_app_de_protecao(sessao))
    pendente = criar_usuario(db, estado=EstadoConta.PENDENTE)
    ativo = criar_usuario(
        db, perfis=(TipoPerfil.PROFISSIONAL,), perfil_ativo=TipoPerfil.PROFISSIONAL
    )
    contratante = criar_usuario(
        db, perfis=(TipoPerfil.CONTRATANTE,), perfil_ativo=TipoPerfil.CONTRATANTE
    )
    admin = criar_usuario(db, papel=PapelUsuario.ADMIN)

    assert cliente.get("/qualquer", headers=cabecalho(pendente)).status_code == 200
    assert (
        cliente.get("/ativa", headers=cabecalho(pendente)).json()["detail"]["codigo"]
        == "conta_pendente"
    )
    assert cliente.get("/ativa", headers=cabecalho(ativo)).status_code == 200
    assert cliente.get("/profissional", headers=cabecalho(ativo)).status_code == 200
    assert cliente.get("/profissional", headers=cabecalho(contratante)).status_code == 403
    assert cliente.get("/admin", headers=cabecalho(ativo)).status_code == 403
    assert cliente.get("/admin", headers=cabecalho(admin)).status_code == 200
