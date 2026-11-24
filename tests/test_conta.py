"""Plano Pro simulado, aceite de termos versionados e exclusão de conta."""

from datetime import timedelta
from io import BytesIO

from app.models import AceiteTermos, Arquivo, EstadoVaga, PerfilProfissional, TipoPerfil
from app.services import conta
from app.tempo import avancar_relogio
from tests.fabricas import (
    SENHA_PADRAO,
    cabecalho,
    criar_cenario_vaga,
    criar_profissional,
    criar_usuario,
)
from tests.test_arquivos import BYTES_JPEG

# ---------------------------------------------------------------- plano Pro


def test_assina_o_plano_pro_e_o_selo_acompanha(cliente, db):
    profissional = criar_profissional(db)
    cab = cabecalho(profissional.usuario)

    antes = cliente.get("/api/v1/assinatura/pro", headers=cab).json()
    assert antes["ativo"] is False
    assert antes["valor_mensal"] > 0

    depois = cliente.post("/api/v1/assinatura/pro/assinar", headers=cab)
    assert depois.status_code == 200
    assert depois.json()["ativo"] is True
    assert depois.json()["renovacao_automatica"] is True
    assert depois.json()["valido_ate"] is not None
    db.expire_all()
    assert db.get(PerfilProfissional, profissional.id).pro is True

    repetida = cliente.post("/api/v1/assinatura/pro/assinar", headers=cab)
    assert repetida.status_code == 409
    assert repetida.json()["detail"]["codigo"] == "assinatura_ativa"


def test_cancelar_mantem_o_plano_ate_o_fim_do_periodo_e_a_varredura_encerra(cliente, db):
    profissional = criar_profissional(db)
    cab = cabecalho(profissional.usuario)
    cliente.post("/api/v1/assinatura/pro/assinar", headers=cab)

    cancelada = cliente.post("/api/v1/assinatura/pro/cancelar", headers=cab).json()
    assert cancelada["ativo"] is True
    assert cancelada["renovacao_automatica"] is False

    avancar_relogio(timedelta(days=40))
    db.expire_all()
    assert conta.renovar_ou_expirar_assinaturas(db) == 1
    db.expire_all()
    assert db.get(PerfilProfissional, profissional.id).pro is False
    assert cliente.get("/api/v1/assinatura/pro", headers=cab).json()["ativo"] is False


def test_assinatura_com_renovacao_automatica_e_renovada_pela_varredura(cliente, db):
    profissional = criar_profissional(db)
    cab = cabecalho(profissional.usuario)
    cliente.post("/api/v1/assinatura/pro/assinar", headers=cab)

    avancar_relogio(timedelta(days=31))
    db.expire_all()
    assert conta.renovar_ou_expirar_assinaturas(db) == 1
    db.expire_all()
    assert db.get(PerfilProfissional, profissional.id).pro is True


def test_cancelar_sem_plano_ativo_e_plano_so_para_profissional(cliente, db):
    profissional = criar_profissional(db)
    sem_plano = cliente.post(
        "/api/v1/assinatura/pro/cancelar", headers=cabecalho(profissional.usuario)
    )
    assert sem_plano.status_code == 409

    contratante = criar_usuario(
        db, perfis=(TipoPerfil.CONTRATANTE,), perfil_ativo=TipoPerfil.CONTRATANTE, verificado=True
    )
    recusado = cliente.post("/api/v1/assinatura/pro/assinar", headers=cabecalho(contratante))
    assert recusado.status_code == 403


# ---------------------------------------------------------------- termos


def test_aceite_dos_termos_e_idempotente_e_exige_a_versao_atual(cliente, db):
    usuario = criar_usuario(db)
    cab = cabecalho(usuario)

    inicial = cliente.get("/api/v1/termos", headers=cab).json()
    assert inicial["aceito"] is False

    desatualizada = cliente.post("/api/v1/termos/aceite", json={"versao": "0.1"}, headers=cab)
    assert desatualizada.status_code == 409
    assert desatualizada.json()["detail"]["versao_atual"] == inicial["versao_atual"]

    versao = inicial["versao_atual"]
    primeiro = cliente.post("/api/v1/termos/aceite", json={"versao": versao}, headers=cab)
    segundo = cliente.post("/api/v1/termos/aceite", json={"versao": versao}, headers=cab)
    assert primeiro.json()["aceito"] is True
    assert segundo.json()["aceito"] is True


# ---------------------------------------------------------------- exclusão da conta


def test_exclusao_exige_a_senha_certa(cliente, db):
    usuario = criar_usuario(db)
    resposta = cliente.post(
        "/api/v1/conta/exclusao", json={"senha": "errada-123"}, headers=cabecalho(usuario)
    )
    assert resposta.status_code == 403
    assert resposta.json()["detail"]["codigo"] == "senha_incorreta"


def test_exclusao_anonimiza_bloqueia_o_acesso_e_libera_o_cpf(cliente, db):
    profissional = criar_profissional(db)
    usuario = profissional.usuario
    cpf_original = profissional.cpf
    email = usuario.email
    cab = cabecalho(usuario)

    resposta = cliente.post("/api/v1/conta/exclusao", json={"senha": SENHA_PADRAO}, headers=cab)

    assert resposta.status_code == 204
    db.expire_all()
    recarregado = db.get(PerfilProfissional, profissional.id)
    assert recarregado.usuario.nome == "Conta excluída"
    assert recarregado.usuario.email != email
    assert recarregado.cpf != cpf_original
    assert recarregado.telefone == ""
    login = cliente.post("/api/v1/auth/login", json={"email": email, "senha": SENHA_PADRAO})
    assert login.status_code == 401
    acesso = cliente.get("/api/v1/auth/me", headers=cab)
    assert acesso.status_code == 403
    assert acesso.json()["detail"]["codigo"] == "conta_excluida"


def test_exclusao_e_barrada_com_vaga_em_andamento(cliente, db):
    cenario = criar_cenario_vaga(db, estado=EstadoVaga.CONTRATADA)

    resposta = cliente.post(
        "/api/v1/conta/exclusao",
        json={"senha": SENHA_PADRAO},
        headers=cabecalho(cenario.contratante),
    )

    assert resposta.status_code == 409
    assert resposta.json()["detail"]["codigo"] == "conta_com_compromissos"


def test_exclusao_apaga_selfie_documento_e_consentimento_de_biometria(cliente, db):
    from app.services import arquivos

    profissional = criar_profissional(db)
    usuario = profissional.usuario
    cab = cabecalho(usuario)
    jpeg = BYTES_JPEG
    chaves = []
    for tipo in ("selfie", "documento"):
        enviado = cliente.post(
            "/api/v1/arquivos",
            data={"tipo": tipo},
            files={"arquivo": ("a.jpg", BytesIO(jpeg), "image/jpeg")},
            headers=cab,
        )
        assert enviado.status_code == 201
        chaves.append(db.get(Arquivo, enviado.json()["id"]).chave)
    cliente.post("/api/v1/verificacao/consentimento", headers=cab)
    armazenamento = arquivos.obter_armazenamento()
    assert all(armazenamento.ler(chave) is not None for chave in chaves)

    resposta = cliente.post("/api/v1/conta/exclusao", json={"senha": SENHA_PADRAO}, headers=cab)

    assert resposta.status_code == 204
    db.expire_all()
    assert db.query(Arquivo).filter_by(usuario_id=usuario.id).count() == 0
    assert db.query(AceiteTermos).filter_by(usuario_id=usuario.id).count() == 0
    assert all(armazenamento.ler(chave) is None for chave in chaves)
