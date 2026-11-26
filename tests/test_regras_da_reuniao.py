"""Regras da reunião de alinhamento: denúncia, pagamento após o serviço, adicionais e mapa."""

import io
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.config import settings
from app.models import (
    Categoria,
    Conversa,
    Denuncia,
    Endereco,
    EstadoConta,
    EstadoDenuncia,
    EstadoVaga,
    Experiencia,
    Notificacao,
    OrigemDenuncia,
    PapelUsuario,
    RiscoCategoria,
    TipoLancamento,
    TipoPerfil,
    Vaga,
)
from app.services import bairros
from app.tempo import fixar_relogio
from tests.fabricas import (
    cabecalho,
    criar_cenario_vaga,
    criar_profissional,
    criar_usuario,
)
from tests.test_arquivos import BYTES_JPEG, BYTES_PDF

BASE = datetime.now(UTC).replace(microsecond=0)


@pytest.fixture
def demo(monkeypatch):
    monkeypatch.setattr(settings, "perfil", "demo")


# ---------------------------------------------------------------- denúncia e chat


def _cenario_com_chat(db):
    cenario = criar_cenario_vaga(db, estado=EstadoVaga.CONTRATADA, aceites_completos=True)
    conversa = Conversa(vaga_id=cenario.vaga.id, aberta=True)
    db.add(conversa)
    db.commit()
    db.refresh(conversa)
    return cenario, conversa


def _enviar(cliente, conversa, usuario, texto="Olá, tudo certo?"):
    return cliente.post(
        f"/api/v1/conversas/{conversa.id}/mensagens",
        json={"texto": texto},
        headers=cabecalho(usuario),
    )


def _denunciar_e_decidir(cliente, db, cenario, conversa, admin, decisao="procedente"):
    criada = cliente.post(
        "/api/v1/denuncias",
        json={"conversa_id": conversa.id, "motivo": "assedio", "descricao": "Mensagens ofensivas."},
        headers=cabecalho(cenario.contratante),
    )
    assert criada.status_code == 201
    return cliente.post(
        f"/api/v1/admin/denuncias/{criada.json()['id']}/decisao",
        json={"decisao": decisao, "motivo": "Análise da administração."},
        headers=cabecalho(admin),
    )


def test_denuncia_procedente_bloqueia_so_o_chat_entre_as_partes(cliente, db):
    cenario, conversa = _cenario_com_chat(db)
    admin = criar_usuario(db, papel=PapelUsuario.ADMIN)
    assert _enviar(cliente, conversa, cenario.contratante).status_code == 201

    decisao = _denunciar_e_decidir(cliente, db, cenario, conversa, admin)

    assert decisao.status_code == 200
    assert decisao.json()["conversa_bloqueada"] is True
    for autor in (cenario.contratante, cenario.profissional):
        bloqueado = _enviar(cliente, conversa, autor)
        assert bloqueado.status_code == 409
        assert bloqueado.json()["detail"]["codigo"] == "conversa_bloqueada"
    anexo = cliente.post(
        f"/api/v1/conversas/{conversa.id}/anexos",
        files={"arquivo": ("foto.jpg", io.BytesIO(BYTES_JPEG), "image/jpeg")},
        headers=cabecalho(cenario.profissional),
    )
    assert anexo.status_code == 409
    # Leitura segue liberada e a conversa informa o bloqueio.
    historico = cliente.get(
        f"/api/v1/conversas/{conversa.id}/mensagens", headers=cabecalho(cenario.profissional)
    )
    assert historico.status_code == 200 and len(historico.json()) == 1
    resumo = cliente.get(f"/api/v1/conversas/{conversa.id}", headers=cabecalho(cenario.contratante))
    assert resumo.json()["bloqueada"] is True


def test_denuncia_procedente_nao_restringe_o_resto_da_plataforma(cliente, db):
    cenario, conversa = _cenario_com_chat(db)
    admin = criar_usuario(db, papel=PapelUsuario.ADMIN)
    _denunciar_e_decidir(cliente, db, cenario, conversa, admin)

    for rota in ("/api/v1/conversas", "/api/v1/contratante/vagas", "/api/v1/notificacoes"):
        assert cliente.get(rota, headers=cabecalho(cenario.contratante)).status_code == 200
    assert (
        cliente.get(
            "/api/v1/profissional/convites", headers=cabecalho(cenario.profissional)
        ).status_code
        == 200
    )
    db.refresh(cenario.profissional)
    assert cenario.profissional.estado == EstadoConta.ATIVA


def test_denuncia_improcedente_nao_bloqueia_o_chat(cliente, db):
    cenario, conversa = _cenario_com_chat(db)
    admin = criar_usuario(db, papel=PapelUsuario.ADMIN)

    decisao = _denunciar_e_decidir(cliente, db, cenario, conversa, admin, "improcedente")

    assert decisao.json()["conversa_bloqueada"] is False
    assert _enviar(cliente, conversa, cenario.profissional).status_code == 201


def test_decisao_notifica_as_duas_partes(cliente, db):
    cenario, conversa = _cenario_com_chat(db)
    admin = criar_usuario(db, papel=PapelUsuario.ADMIN)
    _denunciar_e_decidir(cliente, db, cenario, conversa, admin)

    avisos = db.query(Notificacao).filter(Notificacao.tipo == "denuncia_decidida").all()

    assert {a.usuario_id for a in avisos} == {cenario.contratante.id, cenario.profissional.id}
    para_denunciado = next(a for a in avisos if a.usuario_id == cenario.profissional.id)
    assert "Penalidade 1 de 2" in para_denunciado.mensagem


def test_segunda_penalidade_bane_a_conta_de_forma_permanente(cliente, db):
    admin = criar_usuario(db, papel=PapelUsuario.ADMIN)
    primeiro, conversa1 = _cenario_com_chat(db)
    denunciado = primeiro.profissional
    segundo = criar_cenario_vaga(
        db, estado=EstadoVaga.CONTRATADA, aceites_completos=True, profissional=denunciado
    )
    conversa2 = Conversa(vaga_id=segundo.vaga.id, aberta=True)
    db.add(conversa2)
    db.commit()

    _denunciar_e_decidir(cliente, db, primeiro, conversa1, admin)
    db.refresh(denunciado)
    assert denunciado.estado == EstadoConta.ATIVA

    decisao = _denunciar_e_decidir(cliente, db, segundo, conversa2, admin)

    assert decisao.json()["denunciado_estado"] == "banida"
    db.refresh(denunciado)
    assert denunciado.estado == EstadoConta.BANIDA
    assert denunciado.suspensa_ate is None
    uso = cliente.get("/api/v1/conversas", headers=cabecalho(denunciado))
    assert uso.status_code == 403 and uso.json()["detail"]["codigo"] == "conta_banida"
    login = cliente.post(
        "/api/v1/auth/login", json={"email": denunciado.email, "senha": "senha-segura-123"}
    )
    assert login.status_code == 403 and login.json()["detail"]["codigo"] == "conta_banida"
    banidos = cliente.get("/api/v1/admin/banimentos", headers=cabecalho(admin)).json()
    assert [b["usuario_id"] for b in banidos] == [denunciado.id]
    assert banidos[0]["penalidades"] == 2


def test_denuncia_automatica_improcedente_nao_conta_como_penalidade(cliente, db):
    cenario, conversa = _cenario_com_chat(db)
    db.add(
        Denuncia(
            denunciado_id=cenario.profissional.id,
            vaga_id=cenario.vaga.id,
            conversa_id=conversa.id,
            origem=OrigemDenuncia.AUTOMATICA,
            motivo="contato_externo",
            estado=EstadoDenuncia.IMPROCEDENTE,
        )
    )
    db.commit()

    assert _enviar(cliente, conversa, cenario.contratante).status_code == 201


# ---------------------------------------------------------------- pagamento depois do serviço


def _pronta_para_concluir(db):
    fixar_relogio(BASE)
    cenario = criar_cenario_vaga(
        db,
        aceites_completos=True,
        estado=EstadoVaga.CONTRATADA,
        inicio_em=BASE - timedelta(hours=3),
        duracao_minutos=120,
        valor=Decimal("200.00"),
    )
    return cenario


def test_aceite_das_duas_partes_contrata_sem_pagamento_previo(cliente, db):
    cenario = criar_cenario_vaga(db, estado=EstadoVaga.ACEITA)
    url = f"/api/v1/vagas/{cenario.vaga.id}/acordo/aceite"

    cliente.post(url, headers=cabecalho(cenario.profissional))
    db.refresh(cenario.vaga)
    assert cenario.vaga.estado == EstadoVaga.ACEITA

    cliente.post(url, headers=cabecalho(cenario.contratante))
    db.refresh(cenario.vaga)

    assert cenario.vaga.estado == EstadoVaga.CONTRATADA
    sem = cliente.get(
        f"/api/v1/vagas/{cenario.vaga.id}/pagamento", headers=cabecalho(cenario.contratante)
    )
    assert sem.status_code == 404


def test_pix_e_cobrado_na_conclusao_e_liberado_ao_pagar(cliente, db, demo):
    cenario = _pronta_para_concluir(db)

    conclusao = cliente.post(
        f"/api/v1/vagas/{cenario.vaga.id}/conclusao", headers=cabecalho(cenario.contratante)
    )

    assert conclusao.status_code == 200
    corpo = conclusao.json()
    assert corpo["estado"] == "aguardando" and corpo["codigo_pix"]
    assert corpo["vaga_estado"] == "concluida"

    pago = cliente.post(
        f"/api/v1/pagamentos/{corpo['id']}/simular-confirmacao",
        headers=cabecalho(cenario.contratante),
    )

    assert pago.status_code == 200
    detalhe = pago.json()
    assert detalhe["estado"] == "liberado" and detalhe["vaga_estado"] == "liberada"
    tipos = {item["tipo"]: Decimal(item["valor"]) for item in detalhe["lancamentos"]}
    assert tipos[TipoLancamento.RETENCAO.value] == Decimal("200.00")
    assert tipos[TipoLancamento.LIBERACAO_PROFISSIONAL.value] == Decimal("180.00")
    assert tipos[TipoLancamento.TAXA_PLATAFORMA.value] == Decimal("20.00")
    assert Decimal(detalhe["saldo_retido"]) == Decimal("0.00")


def test_cobranca_vencida_pode_ser_reemitida_depois_da_conclusao(cliente, db, demo):
    cenario = _pronta_para_concluir(db)
    corpo = cliente.post(
        f"/api/v1/vagas/{cenario.vaga.id}/conclusao", headers=cabecalho(cenario.contratante)
    ).json()
    fixar_relogio(BASE + timedelta(minutes=10))

    vencida = cliente.post(
        f"/api/v1/pagamentos/{corpo['id']}/simular-confirmacao",
        headers=cabecalho(cenario.contratante),
    )
    assert vencida.status_code == 409

    nova = cliente.post(
        f"/api/v1/vagas/{cenario.vaga.id}/pagamento", headers=cabecalho(cenario.contratante)
    )

    assert nova.status_code == 201
    assert nova.json()["estado"] == "aguardando" and nova.json()["vaga_estado"] == "concluida"
    assert nova.json()["codigo_pix"] != corpo["codigo_pix"]


def test_disputa_antes_do_pagamento_reembolso_total_cancela_a_cobranca(cliente, db):
    cenario = _pronta_para_concluir(db)
    admin = criar_usuario(db, papel=PapelUsuario.ADMIN)
    aberta = cliente.post(
        f"/api/v1/vagas/{cenario.vaga.id}/disputa",
        json={"descricao": "O serviço não foi feito como combinado."},
        headers=cabecalho(cenario.contratante),
    )
    assert aberta.status_code == 201

    decisao = cliente.post(
        f"/api/v1/admin/disputas/{aberta.json()['id']}/decisao",
        json={"decisao": "reembolso_total", "motivo": "Serviço não prestado."},
        headers=cabecalho(admin),
    )

    assert decisao.status_code == 200
    db.refresh(cenario.vaga)
    assert cenario.vaga.estado == EstadoVaga.REEMBOLSADA


def test_disputa_antes_do_pagamento_parcial_reemite_a_cobranca_reduzida(cliente, db):
    cenario = _pronta_para_concluir(db)
    admin = criar_usuario(db, papel=PapelUsuario.ADMIN)
    aberta = cliente.post(
        f"/api/v1/vagas/{cenario.vaga.id}/disputa",
        json={"descricao": "Metade do serviço ficou incompleta."},
        headers=cabecalho(cenario.contratante),
    )

    cliente.post(
        f"/api/v1/admin/disputas/{aberta.json()['id']}/decisao",
        json={"decisao": "reembolso_parcial", "percentual_reembolso": 40, "motivo": "Parcial."},
        headers=cabecalho(admin),
    )

    pagamento = cliente.get(
        f"/api/v1/vagas/{cenario.vaga.id}/pagamento", headers=cabecalho(cenario.contratante)
    ).json()
    assert pagamento["estado"] == "aguardando"
    assert pagamento["divisao"]["valor_total"] == "120.00"
    assert pagamento["vaga_estado"] == "concluida"


# ---------------------------------------------------------------- comprovantes e adicionais


def _anexar(cliente, vaga, usuario, tipo, valor, *, acordo=None, conteudo=BYTES_PDF, nome="c.pdf"):
    dados = {"tipo": tipo, "valor": str(valor)}
    if acordo is not None:
        dados["acordo_previo"] = acordo
    return cliente.post(
        f"/api/v1/vagas/{vaga.id}/comprovantes",
        data=dados,
        files={"arquivo": (nome, io.BytesIO(conteudo), "application/pdf")},
        headers=cabecalho(usuario),
    )


def test_comprovante_de_servico_nao_paga_taxa_adicional(cliente, db):
    cenario = _pronta_para_concluir(db)

    resposta = _anexar(cliente, cenario.vaga, cenario.contratante, "servico", "200.00")

    assert resposta.status_code == 201
    assert Decimal(resposta.json()["taxa_plataforma"]) == Decimal("0.00")


def test_horas_extras_e_bonus_pagam_sete_por_cento(cliente, db):
    cenario = _pronta_para_concluir(db)

    horas = _anexar(
        cliente,
        cenario.vaga,
        cenario.contratante,
        "horas_extras",
        "100.00",
        acordo="Combinado duas horas extras por R$ 100,00 antes de começar.",
    )
    bonus = _anexar(cliente, cenario.vaga, cenario.contratante, "bonus", "50.00")

    assert horas.status_code == 201 and bonus.status_code == 201
    assert Decimal(horas.json()["taxa_plataforma"]) == Decimal("7.00")
    assert Decimal(bonus.json()["taxa_plataforma"]) == Decimal("3.50")
    resumo = cliente.get(
        f"/api/v1/vagas/{cenario.vaga.id}/comprovantes", headers=cabecalho(cenario.profissional)
    ).json()
    assert Decimal(resumo["total_adicional"]) == Decimal("150.00")
    assert Decimal(resumo["taxa_adicional_plataforma"]) == Decimal("10.50")
    assert Decimal(resumo["liquido_adicional_profissional"]) == Decimal("139.50")


def test_horas_extras_exigem_acordo_previo(cliente, db):
    cenario = _pronta_para_concluir(db)

    resposta = _anexar(cliente, cenario.vaga, cenario.contratante, "horas_extras", "80.00")

    assert resposta.status_code == 422
    assert resposta.json()["detail"]["codigo"] == "acordo_previo_obrigatorio"


def test_so_o_contratante_anexa_adicionais_e_estranhos_nao_veem(cliente, db):
    cenario = _pronta_para_concluir(db)
    estranho = criar_usuario(
        db, perfis=(TipoPerfil.CONTRATANTE,), perfil_ativo=TipoPerfil.CONTRATANTE
    )

    negado = _anexar(cliente, cenario.vaga, cenario.profissional, "bonus", "30.00")
    anexado = _anexar(cliente, cenario.vaga, cenario.contratante, "bonus", "30.00")

    assert negado.status_code == 403
    assert negado.json()["detail"]["codigo"] == "adicional_somente_contratante"
    for rota in (
        f"/api/v1/vagas/{cenario.vaga.id}/comprovantes",
        anexado.json()["arquivo_url"],
    ):
        assert cliente.get(rota, headers=cabecalho(estranho)).status_code == 403
    arquivo = cliente.get(anexado.json()["arquivo_url"], headers=cabecalho(cenario.profissional))
    assert arquivo.status_code == 200 and arquivo.content == BYTES_PDF


def test_comprovante_exige_formato_valido(cliente, db):
    cenario = _pronta_para_concluir(db)

    resposta = _anexar(
        cliente,
        cenario.vaga,
        cenario.contratante,
        "servico",
        "10.00",
        conteudo=b"texto puro",
        nome="x.pdf",
    )

    assert resposta.status_code == 415


def test_adicional_pago_da_destaque_ao_contratante_sem_mexer_no_match(cliente, db):
    cenario = _pronta_para_concluir(db)
    antes = cliente.get(
        "/api/v1/perfis/contratante/meu", headers=cabecalho(cenario.contratante)
    ).json()
    assert antes["destaque"] is False and antes["adicionais_pagos"] == 0

    _anexar(cliente, cenario.vaga, cenario.contratante, "bonus", "40.00")

    depois = cliente.get(
        "/api/v1/perfis/contratante/meu", headers=cabecalho(cenario.contratante)
    ).json()
    assert depois["destaque"] is True and depois["adicionais_pagos"] == 1
    acordo = cliente.get(
        f"/api/v1/vagas/{cenario.vaga.id}/acordo", headers=cabecalho(cenario.profissional)
    ).json()
    assert acordo["contratante"]["destaque"] is True
    assert acordo["profissional"]["destaque"] is False


# ---------------------------------------------------------------- mapa de ofertas


def _categoria(db, slug, risco=RiscoCategoria.BAIXO):
    categoria = Categoria(slug=slug, nome=slug.title(), risco=risco)
    db.add(categoria)
    db.commit()
    return categoria


def _vaga_publicada(db, contratante, categoria, bairro_lat_lon, titulo="Oferta"):
    lat, lon = bairro_lat_lon
    endereco = Endereco(bairro="Torre", latitude=lat, longitude=lon)
    db.add(endereco)
    db.flush()
    vaga = Vaga(
        contratante_id=contratante.perfil_contratante.id,
        categoria_id=categoria.id,
        endereco_id=endereco.id,
        titulo=titulo,
        descricao="Serviço de teste",
        inicio_em=datetime.now(UTC) + timedelta(days=1),
        duracao_minutos=60,
        valor_inicial=Decimal("90.00"),
        estado=EstadoVaga.PUBLICADA,
    )
    db.add(vaga)
    db.commit()
    return vaga


def test_mapa_filtra_por_categoria_experiencia_e_raio(cliente, db):
    contratante = criar_usuario(
        db, perfis=(TipoPerfil.CONTRATANTE,), perfil_ativo=TipoPerfil.CONTRATANTE
    )
    montagem = _categoria(db, "mapa-montagem")
    pintura = _categoria(db, "mapa-pintura")
    jardim = _categoria(db, "mapa-jardim")
    eletrica = _categoria(db, "mapa-eletrica", RiscoCategoria.ALTO)
    perfil = criar_profissional(
        db, categorias=(montagem,), latitude=-8.0500, longitude=-34.9000, raio_km=8
    )
    db.add(Experiencia(profissional_id=perfil.id, categoria_id=pintura.id, descricao="5 anos"))
    db.add(Experiencia(profissional_id=perfil.id, categoria_id=eletrica.id, descricao="2 anos"))
    db.commit()
    perto = _vaga_publicada(db, contratante, montagem, (-8.0520, -34.9020), "Montagem perto")
    exp = _vaga_publicada(db, contratante, pintura, (-8.0600, -34.9100), "Pintura pela experiência")
    arriscada = _vaga_publicada(db, contratante, eletrica, (-8.0510, -34.9010), "Elétrica")
    _vaga_publicada(db, contratante, jardim, (-8.0520, -34.9020), "Fora das categorias")
    _vaga_publicada(db, contratante, montagem, (-8.4000, -35.3000), "Longe demais")

    resposta = cliente.get("/api/v1/profissional/mapa-ofertas", headers=cabecalho(perfil.usuario))

    assert resposta.status_code == 200
    corpo = resposta.json()
    ids = [o["vaga_id"] for o in corpo["ofertas"]]
    assert set(ids) == {perto.id, exp.id, arriscada.id}
    assert [o["distancia_km"] for o in corpo["ofertas"]] == sorted(
        o["distancia_km"] for o in corpo["ofertas"]
    )
    por_id = {o["vaga_id"]: o for o in corpo["ofertas"]}
    assert por_id[perto.id]["pode_atuar"] is True
    assert por_id[arriscada.id]["pode_atuar"] is False
    assert por_id[arriscada.id]["pendencias"] == ["experiencia"]
    assert "mapa-pintura" in corpo["categorias_do_perfil"]


def test_mapa_exige_perfil_profissional(cliente, db):
    contratante = criar_usuario(
        db, perfis=(TipoPerfil.CONTRATANTE,), perfil_ativo=TipoPerfil.CONTRATANTE
    )

    assert cliente.get("/api/v1/profissional/mapa-ofertas").status_code == 401
    assert (
        cliente.get("/api/v1/profissional/mapa-ofertas", headers=cabecalho(contratante)).status_code
        == 403
    )


# ---------------------------------------------------------------- coordenadas dos bairros


def test_coordenadas_do_bairro_sem_acento_e_sem_rede():
    assert bairros.coordenadas_offline("boa viagem") == (-8.1190, -34.9040)
    assert bairros.coordenadas_offline("VARZEA") == bairros.coordenadas_offline("Várzea")
    assert bairros.coordenadas_offline("Bairro Inexistente") is None


def test_vaga_sem_coordenadas_usa_o_centro_do_bairro(cliente, db):
    contratante = criar_usuario(
        db, perfis=(TipoPerfil.CONTRATANTE,), perfil_ativo=TipoPerfil.CONTRATANTE, verificado=True
    )
    categoria = _categoria(db, "bairro-auto")

    resposta = cliente.post(
        "/api/v1/vagas",
        json={
            "titulo": "Montagem de estante",
            "descricao": "Montar uma estante grande",
            "categoria": categoria.slug,
            "valor": 120,
            "duracao_horas": 2,
            "data_inicio": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
            "bairro": "Casa Forte",
        },
        headers=cabecalho(contratante),
    )

    assert resposta.status_code == 201, resposta.text
    corpo = resposta.json()
    assert (corpo["latitude"], corpo["longitude"]) == bairros.coordenadas_offline("Casa Forte")


def test_bairro_desconhecido_sem_coordenadas_e_recusado(monkeypatch):
    monkeypatch.setattr(settings, "geocodificacao_osm", False)

    with pytest.raises(Exception) as erro:
        bairros.resolver_coordenadas("Lugar Nenhum", None, None)

    assert getattr(erro.value, "status_code", None) == 422


def test_geocodificacao_osm_usa_a_rede_apenas_quando_habilitada(monkeypatch):
    chamadas = []
    monkeypatch.setattr(settings, "geocodificacao_osm", True)
    monkeypatch.setattr(
        bairros,
        "geocodificar_osm",
        lambda bairro, cidade="Recife": chamadas.append(bairro) or (-8.2, -34.95),
    )

    assert bairros.resolver_coordenadas("Lugar Nenhum", None, None) == (-8.2, -34.95)
    assert bairros.resolver_coordenadas("Boa Viagem", None, None) == (-8.1190, -34.9040)
    assert bairros.resolver_coordenadas("Qualquer", -8.1, -34.9) == (-8.1, -34.9)
    assert chamadas == ["Lugar Nenhum"]
