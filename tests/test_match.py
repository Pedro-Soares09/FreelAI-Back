from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.models import (
    Avaliacao,
    Configuracao,
    Convite,
    EpiProfissional,
    EstadoConta,
    EstadoConvite,
    EstadoVaga,
    Experiencia,
    Qualificacao,
    RiscoCategoria,
    StatusAprovacao,
    TipoPerfil,
    TipoQualificacao,
)
from app.services import configuracao, match
from app.tempo import agora
from tests.fabricas import (
    LATITUDE_PADRAO,
    criar_categoria,
    criar_profissional,
    criar_usuario,
    criar_vaga,
)

# Deslocamentos de latitude a partir do centro da vaga: 0,01 grau vale cerca de 1,1 km.
PERTO = LATITUDE_PADRAO + 0.02  # ~2,2 km
MEIO = LATITUDE_PADRAO + 0.05  # ~5,6 km
LONGE = LATITUDE_PADRAO + 0.12  # ~13,3 km, fora de um raio de 10 km
MUITO_LONGE = LATITUDE_PADRAO + 0.25  # ~27,8 km, além do raio máximo configurado


@pytest.fixture
def categoria(db):
    return criar_categoria(db)


@pytest.fixture
def vaga(db, categoria):
    return criar_vaga(db, categoria=categoria)


def _configurar(db, **valores):
    perfil = configuracao.perfil_atual()
    for chave, valor in valores.items():
        db.add(Configuracao(chave=chave, perfil=perfil, valor=str(valor)))
    db.commit()


def _avaliar(db, vaga, profissional, nota, visivel_em="agora"):
    if visivel_em == "agora":
        visivel_em = agora()
    db.add(
        Avaliacao(
            vaga_id=vaga.id,
            avaliador_id=vaga.contratante_id,
            avaliado_id=profissional.usuario_id,
            nota=nota,
            visivel_em=visivel_em,
        )
    )
    db.commit()


def _convidar(db, vaga, profissional, posicao=1, estado=EstadoConvite.SUGERIDO, rodada=1):
    db.add(
        Convite(
            vaga_id=vaga.id,
            profissional_id=profissional.id,
            posicao=posicao,
            estado=estado,
            rodada=rodada,
        )
    )
    db.commit()


def _ids(perfis):
    return [p.id for p in perfis]


# Elegibilidade


def test_profissional_completo_dentro_do_raio_e_elegivel(db, categoria, vaga):
    profissional = criar_profissional(db, categorias=(categoria,), latitude=PERTO)

    assert _ids(match.elegiveis(db, vaga)) == [profissional.id]


def test_fora_do_raio_do_profissional_nao_e_elegivel(db, categoria, vaga):
    criar_profissional(db, categorias=(categoria,), latitude=LONGE, raio_km=10)
    dentro = criar_profissional(db, categorias=(categoria,), latitude=LONGE, raio_km=15)

    assert _ids(match.elegiveis(db, vaga)) == [dentro.id]


def test_raio_do_profissional_e_limitado_pelo_maximo_configurado(db, categoria, vaga):
    criar_profissional(db, categorias=(categoria,), latitude=MUITO_LONGE, raio_km=100)

    assert match.elegiveis(db, vaga) == []


def test_sem_localizacao_cadastrada_nao_e_elegivel(db, categoria, vaga):
    criar_profissional(db, categorias=(categoria,), latitude=None, longitude=None)

    assert match.elegiveis(db, vaga) == []


def test_perfil_nao_verificado_nao_e_elegivel(db, categoria, vaga):
    criar_profissional(db, categorias=(categoria,), verificado=False)

    assert match.elegiveis(db, vaga) == []


def test_conta_pendente_ou_suspensa_nao_e_elegivel(db, categoria, vaga):
    criar_profissional(db, categorias=(categoria,), estado=EstadoConta.PENDENTE)
    criar_profissional(db, categorias=(categoria,), estado=EstadoConta.SUSPENSA)

    assert match.elegiveis(db, vaga) == []


def test_categoria_diferente_nao_e_elegivel(db, categoria, vaga):
    outra = criar_categoria(db)
    criar_profissional(db, categorias=(outra,))
    criar_profissional(db, categorias=())

    assert match.elegiveis(db, vaga) == []


def test_categoria_de_risco_exige_experiencia_aprovada(db):
    categoria = criar_categoria(db, risco=RiscoCategoria.MEDIO)
    vaga = criar_vaga(db, categoria=categoria)
    criar_profissional(db, categorias=(categoria,))
    criar_profissional(db, categorias=(categoria,), experiencia_aprovada=StatusAprovacao.RECUSADO)
    aprovado = criar_profissional(
        db, categorias=(categoria,), experiencia_aprovada=StatusAprovacao.APROVADO
    )

    assert _ids(match.elegiveis(db, vaga)) == [aprovado.id]


def test_categoria_que_exige_qualificacao_aceita_so_qualificacao_aprovada(db):
    categoria = criar_categoria(db, exige_qualificacao=True)
    vaga = criar_vaga(db, categoria=categoria)
    criar_profissional(db, categorias=(categoria,))
    pendente = criar_profissional(db, categorias=(categoria,))
    aprovado = criar_profissional(db, categorias=(categoria,))
    db.add_all(
        [
            Qualificacao(
                profissional_id=pendente.id,
                categoria_id=categoria.id,
                tipo=TipoQualificacao.CERTIFICADO,
            ),
            Qualificacao(
                profissional_id=aprovado.id,
                categoria_id=categoria.id,
                tipo=TipoQualificacao.CERTIFICADO,
                status=StatusAprovacao.APROVADO,
            ),
        ]
    )
    db.commit()

    assert _ids(match.elegiveis(db, vaga)) == [aprovado.id]


def test_categoria_que_exige_epi_aceita_so_epi_aprovado(db):
    categoria = criar_categoria(db, exige_epi=True)
    vaga = criar_vaga(db, categoria=categoria)
    criar_profissional(db, categorias=(categoria,))
    recusado = criar_profissional(db, categorias=(categoria,))
    aprovado = criar_profissional(db, categorias=(categoria,))
    db.add_all(
        [
            EpiProfissional(
                profissional_id=recusado.id,
                categoria_id=categoria.id,
                arquivo_url="epi.jpg",
                status=StatusAprovacao.RECUSADO,
            ),
            EpiProfissional(
                profissional_id=aprovado.id,
                categoria_id=categoria.id,
                arquivo_url="epi.jpg",
                status=StatusAprovacao.APROVADO,
            ),
        ]
    )
    db.commit()

    assert _ids(match.elegiveis(db, vaga)) == [aprovado.id]


def test_proprio_contratante_nao_e_elegivel(db, categoria):
    usuario = criar_usuario(
        db, perfis=(TipoPerfil.PROFISSIONAL, TipoPerfil.CONTRATANTE), verificado=True
    )
    vaga = criar_vaga(db, categoria=categoria, contratante=usuario.perfil_contratante)
    proprio = usuario.perfil_profissional
    proprio.endereco_id = vaga.endereco_id
    proprio.raio_km = 10
    proprio.categorias = [categoria]
    db.commit()

    assert match.elegiveis(db, vaga) == []


def test_ocupado_em_horario_sobreposto_nao_e_elegivel(db, categoria, vaga):
    inicio = vaga.inicio_em
    ocupado = criar_profissional(db, categorias=(categoria,))
    criar_vaga(
        db,
        categoria=categoria,
        profissional=ocupado,
        estado=EstadoVaga.CONTRATADA,
        inicio_em=inicio + timedelta(minutes=60),
    )
    adjacente = criar_profissional(db, categorias=(categoria,))
    criar_vaga(
        db,
        categoria=categoria,
        profissional=adjacente,
        estado=EstadoVaga.EM_EXECUCAO,
        inicio_em=inicio + timedelta(minutes=vaga.duracao_minutos),
    )
    encerrada = criar_profissional(db, categorias=(categoria,))
    criar_vaga(
        db,
        categoria=categoria,
        profissional=encerrada,
        estado=EstadoVaga.CONCLUIDA,
        inicio_em=inicio,
    )

    assert _ids(match.elegiveis(db, vaga)) == [adjacente.id, encerrada.id]


def test_ja_convidado_em_qualquer_rodada_nao_e_elegivel(db, categoria, vaga):
    convidado = criar_profissional(db, categorias=(categoria,))
    _convidar(db, vaga, convidado, estado=EstadoConvite.RECUSADO, rodada=1)
    livre = criar_profissional(db, categorias=(categoria,))

    assert _ids(match.elegiveis(db, vaga)) == [livre.id]


# Pontuação


def test_pontuacao_padrao_sem_historico(db, categoria, vaga):
    profissional = criar_profissional(db, categorias=(categoria,))

    # localização 1,0; habilidades 0,5; reputação 4/5; preço 1,0
    esperado = 0.40 * 1.0 + 0.30 * 0.5 + 0.20 * 0.8 + 0.10 * 1.0
    assert match.pontuar(db, vaga, profissional) == pytest.approx(esperado)


def test_localizacao_cai_com_a_distancia(db, categoria, vaga):
    perto = criar_profissional(db, categorias=(categoria,), raio_km=10)
    meio = criar_profissional(db, categorias=(categoria,), latitude=MEIO, raio_km=10)

    diferenca = match.pontuar(db, vaga, perto) - match.pontuar(db, vaga, meio)
    distancia = match.distancia_ate_vaga(db, vaga, meio)
    assert diferenca == pytest.approx(0.40 * distancia / 10)


def test_experiencias_aprovadas_na_categoria_saturam_em_cinco(db, categoria, vaga):
    outra = criar_categoria(db)
    profissional = criar_profissional(db, categorias=(categoria, outra))
    db.add_all(
        [
            Experiencia(
                profissional_id=profissional.id,
                categoria_id=categoria.id,
                descricao="Serviço anterior",
                status=StatusAprovacao.APROVADO,
            )
            for _ in range(7)
        ]
    )
    db.add(
        Experiencia(
            profissional_id=profissional.id,
            categoria_id=categoria.id,
            descricao="Ainda em análise",
        )
    )
    db.commit()
    sem_experiencia = criar_profissional(db, categorias=(categoria,))

    diferenca = match.pontuar(db, vaga, profissional) - match.pontuar(db, vaga, sem_experiencia)
    assert diferenca == pytest.approx(0.30 * 0.5)


def test_reputacao_usa_so_avaliacoes_visiveis(db, categoria, vaga):
    profissional = criar_profissional(db, categorias=(categoria,))
    _avaliar(db, vaga, profissional, nota=2)
    _avaliar(db, criar_vaga(db, categoria=categoria), profissional, nota=4)
    _avaliar(db, criar_vaga(db, categoria=categoria), profissional, nota=5, visivel_em=None)
    _avaliar(
        db,
        criar_vaga(db, categoria=categoria),
        profissional,
        nota=5,
        visivel_em=agora() + timedelta(days=1),
    )
    sem_avaliacao = criar_profissional(db, categorias=(categoria,))

    # média visível 3,0 contra padrão 4,0
    diferenca = match.pontuar(db, vaga, sem_avaliacao) - match.pontuar(db, vaga, profissional)
    assert diferenca == pytest.approx(0.20 * (4.0 - 3.0) / 5)


def test_preco_compara_com_a_media_das_vagas_liberadas(db, categoria):
    vaga = criar_vaga(db, categoria=categoria, valor_inicial=Decimal("100.00"))
    profissional = criar_profissional(db, categorias=(categoria,))
    for valor, estado in (
        ("150.00", EstadoVaga.LIBERADA),
        ("250.00", EstadoVaga.LIBERADA_PARCIAL),
        ("900.00", EstadoVaga.CANCELADA),
    ):
        criar_vaga(
            db,
            categoria=categoria,
            profissional=profissional,
            estado=estado,
            valor_fixado=Decimal(valor),
        )
    sem_historico = criar_profissional(db, categorias=(categoria,))

    # média liberada 200 contra vaga de 100: proximidade 0,5
    diferenca = match.pontuar(db, vaga, sem_historico) - match.pontuar(db, vaga, profissional)
    assert diferenca == pytest.approx(0.10 * 0.5)


def test_preco_usa_o_valor_fixado_quando_existe(db, categoria):
    vaga = criar_vaga(
        db, categoria=categoria, valor_inicial=Decimal("100.00"), valor_fixado=Decimal("200.00")
    )
    profissional = criar_profissional(db, categorias=(categoria,))
    criar_vaga(
        db,
        categoria=categoria,
        profissional=profissional,
        estado=EstadoVaga.LIBERADA,
        valor_fixado=Decimal("200.00"),
    )
    sem_historico = criar_profissional(db, categorias=(categoria,))

    assert match.pontuar(db, vaga, profissional) == pytest.approx(
        match.pontuar(db, vaga, sem_historico)
    )


def test_pesos_vem_da_configuracao(db, categoria, vaga):
    perto_mal_avaliado = criar_profissional(db, categorias=(categoria,))
    _avaliar(db, vaga, perto_mal_avaliado, nota=1)
    longe_sem_avaliacao = criar_profissional(db, categorias=(categoria,), latitude=MEIO)

    assert match.pontuar(db, vaga, perto_mal_avaliado) > match.pontuar(
        db, vaga, longe_sem_avaliacao
    )

    _configurar(db, peso_localizacao=0, peso_habilidades=0, peso_reputacao=1, peso_preco=0)

    assert match.pontuar(db, vaga, perto_mal_avaliado) == pytest.approx(1 / 5)
    assert match.pontuar(db, vaga, longe_sem_avaliacao) == pytest.approx(4 / 5)


def test_pesos_sao_normalizados_e_nota_fica_entre_zero_e_um(db, categoria, vaga):
    profissional = criar_profissional(db, categorias=(categoria,))
    _configurar(db, peso_localizacao=2, peso_habilidades=0, peso_reputacao=0, peso_preco=0)

    assert match.pontuar(db, vaga, profissional) == pytest.approx(1.0)


# Seleção


def test_selecao_sem_novos_devolve_os_tres_melhores_em_ordem(db, categoria, vaga):
    meio = criar_profissional(db, categorias=(categoria,), latitude=MEIO)
    perto = criar_profissional(db, categorias=(categoria,), latitude=PERTO)
    colado = criar_profissional(db, categorias=(categoria,))
    criar_profissional(db, categorias=(categoria,), latitude=LATITUDE_PADRAO + 0.08)

    selecionados = match.selecionar_top3(db, vaga)

    assert [p.id for p, _ in selecionados] == [colado.id, perto.id, meio.id]
    pontuacoes = [nota for _, nota in selecionados]
    assert pontuacoes == sorted(pontuacoes, reverse=True)
    assert all(0 <= nota <= 1 for nota in pontuacoes)


def test_empate_e_desfeito_pela_menor_distancia(db, categoria, vaga):
    _configurar(db, peso_localizacao=0, peso_habilidades=1, peso_reputacao=0, peso_preco=0)
    longe = criar_profissional(db, categorias=(categoria,), latitude=MEIO)
    perto = criar_profissional(db, categorias=(categoria,), latitude=PERTO)

    selecionados = match.selecionar_top3(db, vaga)

    assert selecionados[0][1] == pytest.approx(selecionados[1][1])
    assert [p.id for p, _ in selecionados] == [perto.id, longe.id]


def test_empate_total_e_desfeito_pelo_menor_id(db, categoria, vaga):
    primeiro = criar_profissional(db, categorias=(categoria,))
    segundo = criar_profissional(db, categorias=(categoria,))

    selecionados = match.selecionar_top3(db, vaga)

    assert [p.id for p, _ in selecionados] == [primeiro.id, segundo.id]


def test_menos_de_tres_elegiveis_devolve_todos(db, categoria, vaga):
    criar_profissional(db, categorias=(categoria,))
    criar_profissional(db, categorias=(categoria,), latitude=MEIO)
    criar_profissional(db, categorias=(categoria,), latitude=LONGE)

    assert len(match.selecionar_top3(db, vaga)) == 2


def test_sem_elegiveis_devolve_lista_vazia(db, vaga):
    assert match.selecionar_top3(db, vaga) == []


def test_novo_ocupa_a_terceira_posicao_mesmo_com_pontuacao_menor(db, categoria, vaga):
    primeiro = criar_profissional(db, categorias=(categoria,))
    segundo = criar_profissional(db, categorias=(categoria,), latitude=PERTO)
    criar_profissional(db, categorias=(categoria,), latitude=LATITUDE_PADRAO + 0.03)
    novo = criar_profissional(db, categorias=(categoria,), latitude=MEIO, servicos_concluidos=0)
    criar_profissional(
        db, categorias=(categoria,), latitude=LATITUDE_PADRAO + 0.07, servicos_concluidos=1
    )

    selecionados = match.selecionar_top3(db, vaga)

    assert [p.id for p, _ in selecionados] == [primeiro.id, segundo.id, novo.id]


def test_novo_entre_os_dois_melhores_libera_a_terceira_para_o_geral(db, categoria, vaga):
    novo = criar_profissional(db, categorias=(categoria,), servicos_concluidos=0)
    segundo = criar_profissional(db, categorias=(categoria,), latitude=PERTO)
    terceiro = criar_profissional(db, categorias=(categoria,), latitude=LATITUDE_PADRAO + 0.03)
    criar_profissional(db, categorias=(categoria,), latitude=MEIO, servicos_concluidos=0)

    selecionados = match.selecionar_top3(db, vaga)

    assert [p.id for p, _ in selecionados] == [novo.id, segundo.id, terceiro.id]


def test_so_novos_devolve_os_tres_melhores(db, categoria, vaga):
    perfis = [
        criar_profissional(
            db,
            categorias=(categoria,),
            latitude=LATITUDE_PADRAO + deslocamento,
            servicos_concluidos=0,
        )
        for deslocamento in (0.03, 0.0, 0.05, 0.01)
    ]

    selecionados = match.selecionar_top3(db, vaga)

    assert [p.id for p, _ in selecionados] == [perfis[1].id, perfis[3].id, perfis[0].id]


def test_limite_do_selo_novo_vem_da_configuracao(db, categoria, vaga):
    _configurar(db, servicos_para_deixar_de_ser_novo=20)
    criar_profissional(db, categorias=(categoria,), servicos_concluidos=25)
    criar_profissional(db, categorias=(categoria,), latitude=PERTO, servicos_concluidos=25)
    terceiro = criar_profissional(
        db, categorias=(categoria,), latitude=MEIO, servicos_concluidos=25
    )
    novo = criar_profissional(
        db, categorias=(categoria,), latitude=LONGE, raio_km=15, servicos_concluidos=19
    )

    escolhidos = [p.id for p, _ in match.selecionar_top3(db, vaga)]

    # com o padrão (5 serviços) ninguém seria Novo e o terceiro melhor geral entraria
    assert novo.id in escolhidos
    assert terceiro.id not in escolhidos


# Rodadas


def test_gerar_rodada_cria_convites_sugeridos_em_ordem(db, categoria, vaga):
    perfis = [
        criar_profissional(db, categorias=(categoria,), latitude=LATITUDE_PADRAO + d)
        for d in (0.04, 0.0, 0.02, 0.06)
    ]

    convites = match.gerar_rodada(db, vaga)

    assert [c.profissional_id for c in convites] == [perfis[1].id, perfis[2].id, perfis[0].id]
    assert [c.posicao for c in convites] == [1, 2, 3]
    assert all(c.estado == EstadoConvite.SUGERIDO for c in convites)
    assert all(c.rodada == 1 for c in convites)
    assert all(c.id is not None and 0 < c.pontuacao <= 1 for c in convites)
    assert convites[0].pontuacao >= convites[1].pontuacao >= convites[2].pontuacao
    assert vaga.rodada == 1


def test_gerar_rodada_sem_elegiveis_nao_altera_a_vaga(db, vaga):
    assert match.gerar_rodada(db, vaga) == []
    assert vaga.rodada == 1
    assert vaga.convites == []


def test_gerar_rodada_duas_vezes_seguidas_levanta_rodada_em_andamento(db, categoria, vaga):
    criar_profissional(db, categorias=(categoria,))
    match.gerar_rodada(db, vaga)

    with pytest.raises(HTTPException) as erro:
        match.gerar_rodada(db, vaga)

    assert erro.value.status_code == 409
    assert erro.value.detail["codigo"] == "rodada_em_andamento"
    assert len(vaga.convites) == 1


@pytest.mark.parametrize(
    "estado", [EstadoConvite.SUGERIDO, EstadoConvite.INTERESSADO, EstadoConvite.ESCOLHIDO]
)
def test_convite_aberto_na_rodada_atual_bloqueia_nova_rodada(db, categoria, vaga, estado):
    convidado = criar_profissional(db, categorias=(categoria,))
    _convidar(db, vaga, convidado, estado=estado)
    criar_profissional(db, categorias=(categoria,))

    with pytest.raises(HTTPException):
        match.gerar_rodada(db, vaga)


def test_segunda_rodada_nao_repete_ninguem_e_incrementa_a_rodada(db, categoria, vaga):
    perfis = [
        criar_profissional(db, categorias=(categoria,), latitude=LATITUDE_PADRAO + d)
        for d in (0.0, 0.01, 0.02, 0.03, 0.04)
    ]
    primeira = match.gerar_rodada(db, vaga)
    respostas = (EstadoConvite.RECUSADO, EstadoConvite.EXPIRADO, EstadoConvite.RECUSADO)
    for convite, estado in zip(primeira, respostas, strict=True):
        convite.estado = estado
    db.commit()

    segunda = match.gerar_rodada(db, vaga)

    assert vaga.rodada == 2
    assert [c.profissional_id for c in segunda] == [perfis[3].id, perfis[4].id]
    assert [c.rodada for c in segunda] == [2, 2]
    assert [c.posicao for c in segunda] == [1, 2]
    assert len({c.profissional_id for c in primeira + segunda}) == 5


def test_terceira_rodada_sem_elegiveis_mantem_a_rodada(db, categoria, vaga):
    criar_profissional(db, categorias=(categoria,))
    convite = match.gerar_rodada(db, vaga)[0]
    convite.estado = EstadoConvite.RECUSADO
    db.commit()

    assert match.gerar_rodada(db, vaga) == []
    assert vaga.rodada == 1


def test_plano_pro_nao_altera_a_pontuacao_nem_o_ranking(db, categoria, vaga):
    perto = criar_profissional(db, categorias=(categoria,), latitude=PERTO)
    meio = criar_profissional(db, categorias=(categoria,), latitude=MEIO)
    longe = criar_profissional(db, categorias=(categoria,), latitude=LONGE)
    antes = [(p.id, match.pontuar(db, vaga, p)) for p in (perto, meio, longe)]
    ranking_antes = _ids(p for p, _ in match.selecionar_top3(db, vaga))

    # O pior colocado assina o plano Pro: nota e ordem precisam continuar iguais.
    longe.pro = True
    db.commit()

    depois = [(p.id, match.pontuar(db, vaga, p)) for p in (perto, meio, longe)]
    ranking_depois = _ids(p for p, _ in match.selecionar_top3(db, vaga))
    assert depois == antes
    assert ranking_depois == ranking_antes
