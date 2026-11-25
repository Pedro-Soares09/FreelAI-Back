"""Personalização e visão pública do perfil do contratante, e semente de moderação da demo."""

import io
import sys
from pathlib import Path

from app.models import Categoria, Denuncia, Disputa, EstadoConta, EstadoDenuncia, RiscoCategoria
from app.models.enums import EstadoDisputa
from tests.fabricas import cabecalho, criar_cenario_vaga, criar_usuario
from tests.test_arquivos import BYTES_JPEG, BYTES_PDF

PERFIL = "/api/v1/perfis/contratante/meu"


def _contratante(db):
    from app.models import TipoPerfil

    return criar_usuario(db, perfis=(TipoPerfil.CONTRATANTE,), perfil_ativo=TipoPerfil.CONTRATANTE)


def _enviar_foto(cliente, usuario, conteudo=BYTES_JPEG, nome="rosto.jpg", mime="image/jpeg"):
    return cliente.post(
        "/api/v1/arquivos",
        headers=cabecalho(usuario),
        data={"tipo": "foto"},
        files={"arquivo": (nome, io.BytesIO(conteudo), mime)},
    )


def test_perfil_comeca_com_padroes_e_aceita_personalizacao(cliente, db):
    usuario = _contratante(db)
    db.add(Categoria(slug="pintura-t", nome="Pintura", risco=RiscoCategoria.BAIXO))
    db.commit()

    inicial = cliente.get(PERFIL, headers=cabecalho(usuario)).json()
    assert inicial["cor_destaque"] == "lilas"
    assert inicial["aceita_notificacoes"] is True and inicial["exigir_verificado"] is False

    resposta = cliente.patch(
        PERFIL,
        headers=cabecalho(usuario),
        json={
            "bio": "  Moro em apartamento e contrato serviços pontuais.  ",
            "bairro": "Boa Viagem",
            "horario_contato": "Seg a sex, 18h às 21h",
            "categorias_interesse": ["pintura-t"],
            "exigir_verificado": True,
            "aceita_notificacoes": False,
            "cor_destaque": "azul",
        },
    )
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["bio"] == "Moro em apartamento e contrato serviços pontuais."
    assert corpo["categorias_interesse"] == ["pintura-t"]
    assert corpo["cor_destaque"] == "azul" and corpo["exigir_verificado"] is True
    assert corpo["aceita_notificacoes"] is False


def test_validacoes_do_perfil(cliente, db):
    usuario = _contratante(db)
    cab = cabecalho(usuario)
    assert cliente.patch(PERFIL, headers=cab, json={"bio": "x" * 301}).status_code == 422
    assert cliente.patch(PERFIL, headers=cab, json={"cor_destaque": "vermelho"}).status_code == 422
    erro = cliente.patch(PERFIL, headers=cab, json={"categorias_interesse": ["nao-existe"]})
    assert erro.status_code == 422 and erro.json()["detail"]["codigo"] == "categoria_inexistente"


def test_avatar_por_upload_valida_dono_tipo_e_formato(cliente, db):
    usuario = _contratante(db)
    outro = _contratante(db)
    cab = cabecalho(usuario)

    foto = _enviar_foto(cliente, usuario).json()
    ok = cliente.patch(PERFIL, headers=cab, json={"avatar_arquivo_id": foto["id"]})
    assert ok.status_code == 200 and ok.json()["avatar_url"] == foto["url"]

    alheia = _enviar_foto(cliente, outro).json()
    negado = cliente.patch(PERFIL, headers=cab, json={"avatar_arquivo_id": alheia["id"]})
    assert negado.status_code == 422 and negado.json()["detail"]["codigo"] == "avatar_invalido"

    pdf = cliente.post(
        "/api/v1/arquivos",
        headers=cab,
        data={"tipo": "anexo"},
        files={"arquivo": ("a.pdf", io.BytesIO(BYTES_PDF), "application/pdf")},
    ).json()
    assert (
        cliente.patch(PERFIL, headers=cab, json={"avatar_arquivo_id": pdf["id"]}).status_code == 422
    )

    removido = cliente.patch(PERFIL, headers=cab, json={"avatar_arquivo_id": 0})
    assert removido.json()["avatar_url"] is None


def test_perfil_publico_mostra_metricas_sem_dados_sensiveis(cliente, db):
    cenario = criar_cenario_vaga(db, aceites_completos=True)
    cliente.patch(
        PERFIL,
        headers=cabecalho(cenario.contratante),
        json={"bio": "Contrato com frequência.", "cor_destaque": "verde"},
    )
    resposta = cliente.get(
        f"/api/v1/contratantes/{cenario.contratante.id}/perfil",
        headers=cabecalho(cenario.profissional),
    )
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["bio"] == "Contrato com frequência." and corpo["cor_destaque"] == "verde"
    assert corpo["vagas_publicadas"] == 1 and corpo["servicos_concluidos"] == 0
    assert corpo["membro_desde"] and corpo["avaliacoes"] == []
    for campo in ("documento", "telefone", "selfie_url", "email", "razao_social"):
        assert campo not in corpo


def test_perfil_publico_exige_login_e_404_para_quem_nao_e_contratante(cliente, db):
    usuario = _contratante(db)
    assert cliente.get(f"/api/v1/contratantes/{usuario.id}/perfil").status_code in (401, 403)
    assert (
        cliente.get("/api/v1/contratantes/999999/perfil", headers=cabecalho(usuario)).status_code
        == 404
    )


def test_resumo_da_vaga_traz_avatar_e_cor_do_contratante(cliente, db):
    cenario = criar_cenario_vaga(db)
    foto = _enviar_foto(cliente, cenario.contratante).json()
    cliente.patch(
        PERFIL,
        headers=cabecalho(cenario.contratante),
        json={"avatar_arquivo_id": foto["id"], "cor_destaque": "rosa"},
    )
    acordo = cliente.get(
        f"/api/v1/vagas/{cenario.vaga.id}/acordo", headers=cabecalho(cenario.profissional)
    ).json()
    assert acordo["contratante"]["foto_url"] == foto["url"]
    assert acordo["contratante"]["cor_destaque"] == "rosa"


def test_semente_de_moderacao_e_coerente_e_idempotente(db):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from semear_moderacao import semear_moderacao

    from app.models import Usuario
    from app.models.nucleo import Categoria as Cat

    slugs = {}
    for slug in ("montagem", "limpeza", "eletrica", "pintura", "jardinagem"):
        categoria = Cat(slug=slug, nome=slug.capitalize(), risco=RiscoCategoria.BAIXO)
        db.add(categoria)
        db.flush()
        slugs[slug] = categoria

    semear_moderacao(db, slugs)
    semear_moderacao(db, slugs)  # segunda chamada não duplica

    rafael = db.query(Usuario).filter_by(email="rafael.lima@example.com").one()
    assert rafael.estado == EstadoConta.BANIDA
    procedentes = db.query(Denuncia).filter_by(estado=EstadoDenuncia.PROCEDENTE).count()
    assert procedentes == 4
    assert db.query(Denuncia).filter_by(estado=EstadoDenuncia.ABERTA).count() == 3
    assert db.query(Denuncia).filter_by(estado=EstadoDenuncia.IMPROCEDENTE).count() == 1
    abertas = db.query(Disputa).filter_by(estado=EstadoDisputa.ABERTA).count()
    assert abertas == 3
    assert db.query(Disputa).filter_by(estado=EstadoDisputa.REEMBOLSO_PARCIAL).count() == 1
