"""Testes da US55: avaliação mútua (48h), reputação 70/20/10 e selo Novo até 5 serviços."""

from datetime import UTC, datetime, timedelta

import pytest

from app.models import (
    Advertencia,
    Avaliacao,
    Denuncia,
    EstadoDenuncia,
    EstadoVaga,
    OrigemDenuncia,
    TipoPerfil,
)
from app.services import avaliacoes, pagamentos, varreduras
from app.tempo import agora, avancar_relogio, fixar_relogio
from tests.fabricas import cabecalho, criar_cenario_vaga, criar_usuario

BASE = datetime.now(UTC).replace(microsecond=0)


@pytest.fixture
def cenario_concluido(db):
    fixar_relogio(BASE)
    cenario = criar_cenario_vaga(
        db,
        estado=EstadoVaga.CONCLUIDA,
        inicio_em=BASE - timedelta(hours=2),
        duracao_minutos=120,
    )
    return cenario


# --- Avaliação mútua: ocultação até ambos avaliarem ou o prazo de 48h acabar -------------


def test_avaliacao_de_um_lado_fica_oculta_ate_o_outro_avaliar(cliente, cenario_concluido):
    vaga_id = cenario_concluido.vaga.id
    contratante = cenario_concluido.contratante
    profissional = cenario_concluido.profissional

    # 1. Contratante avalia o profissional
    primeira = cliente.post(
        f"/api/v1/vagas/{vaga_id}/avaliacoes",
        json={"nota": 5, "comentario": "Excelente serviço!"},
        headers=cabecalho(contratante),
    )
    assert primeira.status_code == 201
    corpo_primeira = primeira.json()
    assert corpo_primeira["visivel"] is False
    assert corpo_primeira["visivel_em"] is None
    assert corpo_primeira["avaliador_id"] == contratante.id
    assert corpo_primeira["avaliado_id"] == profissional.id

    # Enquanto o profissional não avalia: a avaliação recebida por ele fica oculta
    visao_prof = cliente.get(
        f"/api/v1/vagas/{vaga_id}/avaliacoes",
        headers=cabecalho(profissional),
    )
    assert visao_prof.status_code == 200
    assert visao_prof.json()["reveladas"] is False
    assert visao_prof.json()["avaliacao_recebida"] is None
    assert visao_prof.json()["avaliacoes"] == []

    # Lista pública do profissional também não exibe a avaliação oculta
    publicas_prof = cliente.get(f"/api/v1/usuarios/{profissional.id}/avaliacoes")
    assert publicas_prof.status_code == 200
    assert publicas_prof.json() == []

    # 2. Profissional avalia o contratante -> ambas são reveladas imediatamente
    segunda = cliente.post(
        f"/api/v1/vagas/{vaga_id}/avaliacoes",
        json={"nota": 4, "comentario": "Contratante atencioso."},
        headers=cabecalho(profissional),
    )
    assert segunda.status_code == 201
    assert segunda.json()["visivel"] is True
    assert segunda.json()["visivel_em"] is not None

    visao_apos = cliente.get(
        f"/api/v1/vagas/{vaga_id}/avaliacoes",
        headers=cabecalho(profissional),
    )
    assert visao_apos.status_code == 200
    dados = visao_apos.json()
    assert dados["reveladas"] is True
    assert dados["minha_avaliacao"]["nota"] == 4
    assert dados["avaliacao_recebida"]["nota"] == 5
    assert len(dados["avaliacoes"]) == 2

    # Listas públicas de ambos agora trazem as avaliações reveladas
    assert len(cliente.get(f"/api/v1/usuarios/{profissional.id}/avaliacoes").json()) == 1
    assert len(cliente.get(f"/api/v1/usuarios/{contratante.id}/avaliacoes").json()) == 1


def test_avaliacao_unilateral_e_revelada_pela_varredura_apos_48h(cliente, db, cenario_concluido):
    vaga_id = cenario_concluido.vaga.id
    contratante = cenario_concluido.contratante
    profissional = cenario_concluido.profissional

    envio = cliente.post(
        f"/api/v1/vagas/{vaga_id}/avaliacoes",
        json={"nota": 5, "comentario": "Muito bom"},
        headers=cabecalho(contratante),
    )
    assert envio.status_code == 201
    assert envio.json()["visivel"] is False

    # Antes de 48h: varredura não revela
    avancar_relogio(timedelta(hours=47, minutes=59))
    assert avaliacoes.revelar_avaliacoes_por_prazo(db) == 0
    assert cliente.get(f"/api/v1/usuarios/{profissional.id}/avaliacoes").json() == []

    # Ao completar 48h: varredura revela a avaliação
    avancar_relogio(timedelta(minutes=2))
    assert "revelacao_avaliacoes" in varreduras.nomes_registrados()
    assert avaliacoes.revelar_avaliacoes_por_prazo(db) == 1
    assert avaliacoes.revelar_avaliacoes_por_prazo(db) == 0

    lista = cliente.get(f"/api/v1/usuarios/{profissional.id}/avaliacoes").json()
    assert len(lista) == 1
    assert lista[0]["nota"] == 5
    assert lista[0]["visivel"] is True


def test_avaliar_apos_48h_ou_duas_vezes_ou_antes_da_conclusao_e_recusado(
    cliente, db, cenario_concluido
):
    vaga_id = cenario_concluido.vaga.id
    contratante = cenario_concluido.contratante
    terceiro = criar_usuario(db, perfis=(TipoPerfil.CONTRATANTE,))

    # Terceiro não avalia
    assert (
        cliente.post(
            f"/api/v1/vagas/{vaga_id}/avaliacoes",
            json={"nota": 5},
            headers=cabecalho(terceiro),
        ).status_code
        == 403
    )

    # Primeira avaliação funciona
    assert (
        cliente.post(
            f"/api/v1/vagas/{vaga_id}/avaliacoes",
            json={"nota": 5},
            headers=cabecalho(contratante),
        ).status_code
        == 201
    )

    # Segunda avaliação da mesma parte é recusada com 409
    duplicada = cliente.post(
        f"/api/v1/vagas/{vaga_id}/avaliacoes",
        json={"nota": 4},
        headers=cabecalho(contratante),
    )
    assert duplicada.status_code == 409
    assert duplicada.json()["detail"]["codigo"] == "avaliacao_ja_registrada"

    # Após 48h, o outro lado não consegue mais enviar avaliação
    avancar_relogio(timedelta(hours=48, minutes=1))
    atrasada = cliente.post(
        f"/api/v1/vagas/{vaga_id}/avaliacoes",
        json={"nota": 5},
        headers=cabecalho(cenario_concluido.profissional),
    )
    assert atrasada.status_code == 409
    assert atrasada.json()["detail"]["codigo"] == "prazo_avaliacao_encerrado"

    # Vaga ainda em execução não pode ser avaliada
    em_andamento = criar_cenario_vaga(db, estado=EstadoVaga.EM_EXECUCAO)
    cedo = cliente.post(
        f"/api/v1/vagas/{em_andamento.vaga.id}/avaliacoes",
        json={"nota": 5},
        headers=cabecalho(em_andamento.contratante),
    )
    assert cedo.status_code == 409
    assert cedo.json()["detail"]["codigo"] == "vaga_nao_avaliavel"


# --- Fórmula de Reputação 70/20/10 (exemplos documentados no README) --------------------


@pytest.mark.parametrize(
    ("nota_media", "taxa_canc", "denuncias", "esperado"),
    [
        # Exemplo 1 do README: Conta nova (neutra 4,0; 0% cancelamento; 0 denúncias) -> 4,30
        (None, 0.0, 0, 4.30),
        # Exemplo 2 do README: Exemplar (média 5,0; 0% cancelamento; 0 denúncias) -> 5,00
        (5.0, 0.0, 0, 5.00),
        # Exemplo 3 do README: Média 4,5; 1/20 cancelamento tardio (5%); 1 denúncia -> 4,40
        (4.5, 1 / 20, 1, 4.40),
        # Exemplo 4 do README: Média 3,0; 5/20 cancelamentos (25%); 3 denúncias -> 2,85
        (3.0, 5 / 20, 3, 2.85),
    ],
)
def test_formula_reputacao_70_20_10_exemplos_do_readme(
    nota_media, taxa_canc, denuncias, esperado
):
    reputacao, _, _, _ = avaliacoes.formula_reputacao(
        nota_media=nota_media,
        taxa_cancelamento_tardio=taxa_canc,
        denuncias_procedentes=denuncias,
    )

    assert reputacao == pytest.approx(esperado)


def test_endpoint_reputacao_calcula_70_20_10_com_dados_do_banco(cliente, db):
    cenario = criar_cenario_vaga(db, estado=EstadoVaga.CONCLUIDA)
    profissional = cenario.profissional
    contratante = cenario.contratante

    # 1. Sem avaliações, sem cancelamentos, sem denúncias -> 4,30 (média neutra 4,0)
    inicial = cliente.get(f"/api/v1/usuarios/{profissional.id}/reputacao")
    assert inicial.status_code == 200
    assert inicial.json()["reputacao"] == pytest.approx(4.30)
    assert inicial.json()["nota_media"] is None
    assert inicial.json()["media_considerada"] == pytest.approx(4.0)

    # 2. Adiciona avaliações visíveis com média 4,5 (notas 5 e 4),
    # 1 advertência de cancelamento tardio e 1 denúncia procedente (+ 1 improcedente ignorada)
    segunda_vaga = criar_cenario_vaga(
        db,
        contratante=contratante,
        profissional=profissional,
        estado=EstadoVaga.CONCLUIDA,
    ).vaga
    instante = agora()
    db.add_all(
        [
            Avaliacao(
                vaga_id=cenario.vaga.id,
                avaliador_id=contratante.id,
                avaliado_id=profissional.id,
                nota=5,
                visivel_em=instante,
            ),
            Avaliacao(
                vaga_id=segunda_vaga.id,
                avaliador_id=contratante.id,
                avaliado_id=profissional.id,
                nota=4,
                visivel_em=instante,
            ),
            Advertencia(
                usuario_id=profissional.id,
                vaga_id=cenario.vaga.id,
                motivo="Cancelamento tardio",
            ),
            Denuncia(
                denunciante_id=contratante.id,
                denunciado_id=profissional.id,
                vaga_id=cenario.vaga.id,
                origem=OrigemDenuncia.USUARIO,
                motivo="Atraso grave",
                estado=EstadoDenuncia.PROCEDENTE,
            ),
            Denuncia(
                denunciante_id=contratante.id,
                denunciado_id=profissional.id,
                vaga_id=segunda_vaga.id,
                origem=OrigemDenuncia.USUARIO,
                motivo="Improcedente",
                estado=EstadoDenuncia.IMPROCEDENTE,
            ),
        ]
    )
    db.commit()

    # Exemplo 3 do README: 0,70*4,5 + 0,20*4,75 + 0,10*3,0 = 3,15 + 0,95 + 0,30 = 4,40
    calculada = cliente.get(f"/api/v1/usuarios/{profissional.id}/reputacao").json()
    assert calculada["nota_media"] == pytest.approx(4.5)
    assert calculada["total_avaliacoes"] == 2
    assert calculada["cancelamentos_tardios_ultimos_20"] == 1
    assert calculada["nota_cancelamentos"] == pytest.approx(4.75)
    assert calculada["denuncias_procedentes"] == 1
    assert calculada["nota_denuncias"] == pytest.approx(3.0)
    assert calculada["reputacao"] == pytest.approx(4.40)


# --- Selo Novo: ativo até 5 serviços concluídos; some ao concluir o 6º ------------------


def test_selo_novo_permanece_ate_5_servicos_e_some_ao_concluir_o_sexto(cliente, db):
    fixar_relogio(BASE)
    profissional_usuario = criar_usuario(
        db,
        perfis=(TipoPerfil.PROFISSIONAL,),
        perfil_ativo=TipoPerfil.PROFISSIONAL,
        verificado=True,
    )
    perfil = profissional_usuario.perfil_profissional
    assert perfil.servicos_concluidos == 0

    # De 0 até 5 serviços concluídos, o selo Novo permanece True
    for esperado_concluidos in range(1, 6):
        cenario = criar_cenario_vaga(
            db,
            profissional=profissional_usuario,
            estado=EstadoVaga.CONTRATADA,
            inicio_em=BASE - timedelta(hours=3),
            duracao_minutos=60,
        )
        pagamentos.confirmar_conclusao(db, cenario.vaga, cenario.contratante.id)
        db.refresh(perfil)
        assert perfil.servicos_concluidos == esperado_concluidos

        rep = cliente.get(f"/api/v1/usuarios/{profissional_usuario.id}/reputacao").json()
        assert rep["servicos_concluidos"] == esperado_concluidos
        assert rep["novo"] is True

    # Ao concluir o 6º serviço, o selo Novo desaparece (novo == False)
    sexto = criar_cenario_vaga(
        db,
        profissional=profissional_usuario,
        estado=EstadoVaga.CONTRATADA,
        inicio_em=BASE - timedelta(hours=3),
        duracao_minutos=60,
    )
    pagamentos.confirmar_conclusao(db, sexto.vaga, sexto.contratante.id)
    db.refresh(perfil)
    assert perfil.servicos_concluidos == 6

    rep_sexto = cliente.get(f"/api/v1/usuarios/{profissional_usuario.id}/reputacao").json()
    assert rep_sexto["servicos_concluidos"] == 6
    assert rep_sexto["novo"] is False
