"""Casos de borda: concorrência, prazos, validações e limites de pagamento, conta e match."""

from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.schemas.auth import RegistroEntrada
from app.config import SEGREDO_PADRAO, Settings
from app.models import (
    Avaliacao,
    Configuracao,
    Disputa,
    EstadoConta,
    EstadoDisputa,
    EstadoPagamento,
    EstadoVaga,
    Lancamento,
    PapelUsuario,
    TipoLancamento,
    TipoPerfil,
)
from app.services import configuracao, match, pagamentos
from app.tempo import agora, avancar_relogio, como_utc, fixar_relogio
from tests.fabricas import (
    SENHA_PADRAO,
    cabecalho,
    criar_categoria,
    criar_cenario_vaga,
    criar_profissional,
    criar_usuario,
    criar_vaga,
)

# --- Pagamento ---------------------------------------------------------------------------


def _retido(db):
    cenario = criar_cenario_vaga(db, aceites_completos=True)
    pagamento = pagamentos.criar_pagamento(db, cenario.vaga)
    pagamentos.reter(db, pagamento)
    return cenario, pagamento


def test_sessao_com_estado_desatualizado_nao_duplica_a_liberacao(engine, db):
    from sqlalchemy.orm import sessionmaker

    cenario, pagamento = _retido(db)
    outra = sessionmaker(bind=engine)()
    try:
        defasado = outra.get(type(pagamento), pagamento.id)
        assert defasado.estado == EstadoPagamento.RETIDO

        pagamentos.liberar_integral(db, pagamento)
        pagamentos.liberar_integral(outra, defasado)

        lancamentos = outra.scalars(select(Lancamento)).all()
        assert sorted(item.tipo.value for item in lancamentos) == [
            "liberacao_profissional",
            "retencao",
            "taxa_plataforma",
        ]
    finally:
        outra.close()


def test_sessao_desatualizada_nao_estorna_pagamento_ja_liberado(engine, db):
    from sqlalchemy.orm import sessionmaker

    cenario, pagamento = _retido(db)
    outra = sessionmaker(bind=engine)()
    try:
        defasado = outra.get(type(pagamento), pagamento.id)
        pagamentos.liberar_integral(db, pagamento)

        with pytest.raises(HTTPException) as erro:
            pagamentos.estornar_integral(outra, defasado)

        assert erro.value.status_code == 409
        assert defasado.estado == EstadoPagamento.LIBERADO
    finally:
        outra.close()


def test_livro_recusa_o_mesmo_tipo_de_lancamento_duas_vezes(db):
    cenario, pagamento = _retido(db)

    db.add(
        Lancamento(pagamento_id=pagamento.id, tipo=TipoLancamento.RETENCAO, valor=Decimal("1.00"))
    )
    with pytest.raises(IntegrityError):
        db.commit()


def test_cobranca_vencida_e_reaberta_sem_esperar_a_varredura(db):
    cenario = criar_cenario_vaga(db, aceites_completos=True)
    pagamento = pagamentos.criar_pagamento(db, cenario.vaga)
    avancar_relogio(timedelta(hours=1))

    novo = pagamentos.criar_pagamento(db, cenario.vaga)

    assert novo.id == pagamento.id
    assert novo.estado == EstadoPagamento.AGUARDANDO
    assert como_utc(novo.expira_em) > agora()
    assert cenario.vaga.estado == EstadoVaga.AGUARDANDO_PAGAMENTO


def test_cobranca_reaberta_recebe_codigo_pix_novo(db):
    cenario = criar_cenario_vaga(db, aceites_completos=True)
    primeiro = pagamentos.criar_pagamento(db, cenario.vaga).codigo_pix
    avancar_relogio(timedelta(hours=1))

    segundo = pagamentos.criar_pagamento(db, cenario.vaga).codigo_pix

    assert primeiro != segundo


def test_vaga_com_valor_fixado_zero_nao_gera_cobranca(db):
    cenario = criar_cenario_vaga(db, aceites_completos=True, valor_fixado=Decimal("0.00"))

    with pytest.raises(HTTPException) as erro:
        pagamentos.criar_pagamento(db, cenario.vaga)

    assert erro.value.detail["codigo"] == "valor_invalido"


def test_taxa_fracionaria_e_respeitada(db):
    db.add(
        Configuracao(chave="taxa_plataforma_pct", perfil="producao", valor="12.5", descricao="x")
    )
    db.commit()

    liquido, taxa = pagamentos.calcular_divisao(Decimal("150.00"), db=db)

    assert (liquido, taxa) == (Decimal("131.25"), Decimal("18.75"))


def test_percentuais_de_taxa_e_liberacao_aceitam_fracao():
    assert configuracao.PARAMETROS["taxa_plataforma_pct"].inteiro is False
    assert configuracao.PARAMETROS["liberacao_parcial_pct"].inteiro is False


def test_liberacao_parcial_com_percentual_invalido_nao_derruba_a_varredura(db):
    cenario, pagamento = _retido(db)
    cenario.vaga.estado = EstadoVaga.CONCLUIDA
    db.add(
        Configuracao(chave="liberacao_parcial_pct", perfil="producao", valor="150", descricao="x")
    )
    db.commit()
    avancar_relogio(timedelta(days=10))

    assert pagamentos.liberar_parcialmente_sem_validacao(db) == 0
    assert pagamento.estado == EstadoPagamento.RETIDO


def test_validacao_facial_nao_libera_com_disputa_aberta(db):
    cenario, pagamento = _retido(db)
    cenario.vaga.estado = EstadoVaga.CONCLUIDA
    db.add(
        Disputa(
            vaga_id=cenario.vaga.id,
            aberta_por_id=cenario.contratante.id,
            descricao="Serviço não executado",
            estado=EstadoDisputa.ABERTA,
        )
    )
    db.commit()

    with pytest.raises(HTTPException) as erro:
        pagamentos.validar_facial_e_liberar(db, cenario.vaga, cenario.contratante)

    assert erro.value.detail["codigo"] == "vaga_em_disputa"
    assert pagamento.estado == EstadoPagamento.RETIDO


def test_falta_so_vale_depois_da_tolerancia(db):
    cenario, pagamento = _retido(db)
    inicio = como_utc(cenario.vaga.inicio_em)

    fixar_relogio(inicio + timedelta(minutes=5))
    with pytest.raises(HTTPException) as erro:
        pagamentos.registrar_falta(db, cenario.vaga)
    assert erro.value.detail["codigo"] == "tolerancia_em_andamento"
    assert pagamento.estado == EstadoPagamento.RETIDO

    fixar_relogio(inicio + timedelta(minutes=31))
    pagamentos.registrar_falta(db, cenario.vaga)
    assert pagamento.estado == EstadoPagamento.ESTORNADO


def _api_retido(cliente, db):
    contratante = criar_usuario(
        db, perfis=(TipoPerfil.CONTRATANTE,), perfil_ativo=TipoPerfil.CONTRATANTE
    )
    cenario = criar_cenario_vaga(db, aceites_completos=True, contratante=contratante)
    return contratante, cenario


def test_rota_de_falta_exige_o_contratante_dono_e_a_tolerancia(cliente, db, monkeypatch):
    from app.config import settings

    contratante, cenario = _api_retido(cliente, db)
    pagamento = pagamentos.criar_pagamento(db, cenario.vaga)
    pagamentos.reter(db, pagamento)
    outro = criar_usuario(db, perfis=(TipoPerfil.CONTRATANTE,), perfil_ativo=TipoPerfil.CONTRATANTE)
    caminho = f"/api/v1/vagas/{cenario.vaga.id}/falta"

    assert cliente.post(caminho).status_code == 401
    assert cliente.post(caminho, headers=cabecalho(outro)).status_code == 403
    cedo = cliente.post(caminho, headers=cabecalho(contratante))
    assert cedo.status_code == 409

    fixar_relogio(como_utc(cenario.vaga.inicio_em) + timedelta(minutes=31))
    ok = cliente.post(caminho, headers=cabecalho(contratante))

    assert ok.status_code == 200
    assert ok.json()["estado"] == "estornado"
    assert ok.json()["saldo_retido"] == "0.00"
    assert settings.perfil in {"producao", "demo"}


def test_datas_da_resposta_do_pagamento_trazem_fuso(cliente, db):
    contratante, cenario = _api_retido(cliente, db)

    resposta = cliente.post(
        f"/api/v1/vagas/{cenario.vaga.id}/pagamento", headers=cabecalho(contratante)
    )

    assert resposta.status_code == 201
    corpo = resposta.json()
    for campo in ("expira_em", "criado_em", "atualizado_em"):
        assert corpo[campo].endswith(("Z", "+00:00")), (campo, corpo[campo])


def test_divisao_da_resposta_reflete_a_liberacao_parcial(cliente, db):
    contratante, cenario = _api_retido(cliente, db)
    pagamento = pagamentos.criar_pagamento(db, cenario.vaga)
    pagamentos.reter(db, pagamento)
    pagamentos.liberar_parcial(db, pagamento, 50)

    resposta = cliente.get(
        f"/api/v1/vagas/{cenario.vaga.id}/pagamento", headers=cabecalho(contratante)
    )

    divisao = resposta.json()["divisao"]
    assert divisao["valor_liquido_profissional"] == "67.50"
    assert divisao["taxa_plataforma"] == "7.50"


def test_simulacao_exige_contratante_ativo(cliente, db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "perfil", "demo")
    pendente = criar_usuario(
        db,
        estado=EstadoConta.PENDENTE,
        perfis=(TipoPerfil.CONTRATANTE,),
        perfil_ativo=TipoPerfil.CONTRATANTE,
    )
    cenario = criar_cenario_vaga(db, aceites_completos=True, contratante=pendente)
    pagamento = pagamentos.criar_pagamento(db, cenario.vaga)

    resposta = cliente.post(
        f"/api/v1/pagamentos/{pagamento.id}/simular-confirmacao", headers=cabecalho(pendente)
    )

    assert resposta.status_code == 403


# --- Autenticação ------------------------------------------------------------------------


def test_senha_acima_de_72_bytes_e_recusada_mesmo_com_poucos_caracteres():
    with pytest.raises(ValidationError):
        RegistroEntrada(nome="Ana", email="ana@example.com", senha="ã" * 40)

    RegistroEntrada(nome="Ana", email="ana@example.com", senha="ã" * 36)


def test_nome_em_branco_e_recusado_e_nome_e_aparado():
    with pytest.raises(ValidationError):
        RegistroEntrada(nome="   ", email="ana@example.com", senha=SENHA_PADRAO)

    entrada = RegistroEntrada(nome="  Ana  ", email="ana@example.com", senha=SENHA_PADRAO)
    assert entrada.nome == "Ana"


def test_login_antigo_tambem_barra_conta_banida(cliente, db):
    usuario = criar_usuario(db, estado=EstadoConta.BANIDA, email="banida@example.com")

    resposta = cliente.post("/login", json={"email": usuario.email, "senha": SENHA_PADRAO})

    assert resposta.status_code == 403


def test_me_devolve_data_de_suspensao_com_fuso(cliente, db):
    usuario = criar_usuario(db, estado=EstadoConta.SUSPENSA)
    usuario.suspensa_ate = agora() + timedelta(days=3)
    db.commit()

    resposta = cliente.get("/api/v1/auth/me", headers=cabecalho(usuario))

    # Conta suspensa é barrada; a data de retorno vem no erro, já com fuso.
    assert resposta.status_code == 403
    assert resposta.json()["detail"]["retorno_em"].endswith("+00:00")


def test_producao_recusa_segredo_padrao_ou_curto():
    with pytest.raises(ValidationError):
        Settings(ambiente="producao", secret_key=SEGREDO_PADRAO, _env_file=None)
    with pytest.raises(ValidationError):
        Settings(ambiente="producao", secret_key="curto", _env_file=None)
    with pytest.raises(ValidationError):
        Settings(ambiente="producao", secret_key="replace-this-" + "x" * 40, _env_file=None)

    forte = Settings(ambiente="producao", secret_key="k" * 48, _env_file=None)
    assert forte.ambiente == "producao"
    assert Settings(ambiente="desenvolvimento", _env_file=None).secret_key == SEGREDO_PADRAO


# --- Banco e match -----------------------------------------------------------------------


def test_chaves_estrangeiras_estao_ativas_no_sqlite(db):
    from app.models import Vaga

    db.add(
        Vaga(
            contratante_id=999,
            categoria_id=999,
            endereco_id=999,
            titulo="Órfã",
            descricao="Sem referências válidas",
            inicio_em=agora(),
            duracao_minutos=60,
            valor_inicial=Decimal("10.00"),
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()


def test_restricoes_de_dominio_do_banco(db):
    cenario = criar_cenario_vaga(db, aceites_completos=True)
    db.add(
        Avaliacao(
            vaga_id=cenario.vaga.id,
            avaliador_id=cenario.contratante.id,
            avaliado_id=cenario.profissional.id,
            nota=7,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    profissional = criar_profissional(db)
    profissional.raio_km = -3
    with pytest.raises(IntegrityError):
        db.commit()


def test_rodada_so_para_vaga_publicada(db):
    categoria = criar_categoria(db)
    criar_profissional(db, categorias=(categoria,))
    vaga = criar_vaga(db, categoria=categoria, estado=EstadoVaga.CANCELADA)

    with pytest.raises(HTTPException) as erro:
        match.gerar_rodada(db, vaga)

    assert erro.value.detail["codigo"] == "vaga_indisponivel"


def test_gerar_rodada_duas_vezes_em_sessoes_diferentes_resulta_em_conflito(engine, db):
    from sqlalchemy.orm import sessionmaker

    categoria = criar_categoria(db)
    criar_profissional(db, categorias=(categoria,))
    vaga = criar_vaga(db, categoria=categoria)
    outra: Session = sessionmaker(bind=engine)()
    try:
        vaga_defasada = outra.get(type(vaga), vaga.id)
        match.gerar_rodada(db, vaga)

        with pytest.raises(HTTPException) as erro:
            match.gerar_rodada(outra, vaga_defasada)

        assert erro.value.status_code == 409
    finally:
        outra.close()


def test_administrador_nao_e_afetado_pelas_novas_regras(cliente, db):
    admin = criar_usuario(db, papel=PapelUsuario.ADMIN)

    assert cliente.get("/api/v1/admin/configuracoes", headers=cabecalho(admin)).status_code == 200
