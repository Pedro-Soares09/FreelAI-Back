from app.models import Avaliacao
from app.tempo import agora
from tests.fabricas import cabecalho, criar_cenario_vaga


def _url(usuario_id: int) -> str:
    return f"/api/v1/profissionais/{usuario_id}/perfil"


def test_perfil_publico_do_profissional_mostra_reputacao_sem_dados_sensiveis(cliente, db):
    cenario = criar_cenario_vaga(db, aceites_completos=True)

    resposta = cliente.get(_url(cenario.profissional.id), headers=cabecalho(cenario.contratante))

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["usuario_id"] == cenario.profissional.id
    assert corpo["nome"] == cenario.profissional.nome
    assert corpo["novo"] is True and corpo["total_avaliacoes"] == 0
    assert corpo["avaliacoes"] == [] and corpo["membro_desde"]
    for campo in ("cpf", "telefone", "latitude", "longitude", "data_nascimento", "email"):
        assert campo not in corpo


def test_perfil_publico_lista_so_avaliacoes_ja_visiveis(cliente, db):
    cenario = criar_cenario_vaga(db, aceites_completos=True)
    db.add(
        Avaliacao(
            vaga_id=cenario.vaga.id,
            avaliador_id=cenario.contratante.id,
            avaliado_id=cenario.profissional.id,
            nota=5,
            comentario="Serviço impecável.",
            visivel_em=agora(),
        )
    )
    db.commit()

    corpo = cliente.get(
        _url(cenario.profissional.id), headers=cabecalho(cenario.contratante)
    ).json()

    assert corpo["total_avaliacoes"] == 1 and corpo["nota_media"] == 5.0
    assert corpo["avaliacoes"][0]["comentario"] == "Serviço impecável."
    assert corpo["avaliacoes"][0]["avaliador_nome"] == cenario.contratante.nome


def test_perfil_publico_exige_login_e_devolve_404_para_quem_nao_e_profissional(cliente, db):
    cenario = criar_cenario_vaga(db)

    assert cliente.get(_url(cenario.profissional.id)).status_code in (401, 403)
    assert (
        cliente.get(
            _url(cenario.contratante.id), headers=cabecalho(cenario.profissional)
        ).status_code
        == 404
    )
