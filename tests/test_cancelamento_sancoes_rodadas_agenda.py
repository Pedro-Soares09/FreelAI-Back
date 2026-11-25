from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import (
    Advertencia,
    Avaliacao,
    BloqueioAgenda,
    Convite,
    EstadoConta,
    EstadoConvite,
    EstadoVaga,
    Notificacao,
    PapelUsuario,
    TipoPerfil,
)
from app.services import sancoes, varreduras
from app.tempo import agora, fixar_relogio
from tests.fabricas import (
    SENHA_PADRAO,
    cabecalho,
    criar_categoria,
    criar_cenario_vaga,
    criar_profissional,
    criar_usuario,
    criar_vaga,
)

BASE = datetime.now(UTC).replace(microsecond=0)


def _admin(db):
    return criar_usuario(db, papel=PapelUsuario.ADMIN)


def _cenario(db, *, em_horas, estado=EstadoVaga.CONTRATADA):
    fixar_relogio(BASE)
    return criar_cenario_vaga(
        db, estado=estado, inicio_em=BASE + timedelta(hours=em_horas), aceites_completos=True
    )


# ----------------------------------------------------------------------- cancelamento


def test_previa_avisa_da_advertencia_quando_faltam_menos_de_4_horas(cliente, db):
    cenario = _cenario(db, em_horas=2)

    resposta = cliente.get(
        f"/api/v1/vagas/{cenario.vaga.id}/cancelamento", headers=cabecalho(cenario.contratante)
    )

    assert resposta.status_code == 200
    assert resposta.json()["gera_advertencia"] is True
    assert resposta.json()["limite_horas"] == 4


def test_cancelar_com_antecedencia_nao_gera_advertencia_e_notifica_a_outra_parte(cliente, db):
    cenario = _cenario(db, em_horas=10)

    resposta = cliente.post(
        f"/api/v1/vagas/{cenario.vaga.id}/cancelamento",
        json={"motivo": "Imprevisto no trabalho"},
        headers=cabecalho(cenario.contratante),
    )

    assert resposta.status_code == 200
    assert resposta.json()["advertencia_aplicada"] is False
    db.refresh(cenario.vaga)
    assert cenario.vaga.estado == EstadoVaga.CANCELADA
    assert db.scalars(select(Advertencia)).first() is None
    avisos = db.scalars(
        select(Notificacao).where(
            Notificacao.usuario_id == cenario.profissional.id, Notificacao.tipo == "vaga_cancelada"
        )
    ).all()
    assert len(avisos) == 1


def test_cancelar_tarde_gera_advertencia_para_quem_cancelou(cliente, db):
    cenario = _cenario(db, em_horas=2)

    resposta = cliente.post(
        f"/api/v1/vagas/{cenario.vaga.id}/cancelamento",
        json={"motivo": "Não vou poder ir"},
        headers=cabecalho(cenario.profissional),
    )

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["advertencia_aplicada"] is True
    assert corpo["advertencias_no_periodo"] == 1
    advertencia = db.scalars(select(Advertencia)).one()
    assert advertencia.usuario_id == cenario.profissional.id


def test_tres_cancelamentos_tardios_suspendem_a_conta_por_7_dias(cliente, db):
    profissional = criar_usuario(
        db, perfis=(TipoPerfil.PROFISSIONAL,), perfil_ativo=TipoPerfil.PROFISSIONAL
    )
    fixar_relogio(BASE)
    for _ in range(3):
        cenario = criar_cenario_vaga(
            db,
            estado=EstadoVaga.CONTRATADA,
            inicio_em=BASE + timedelta(hours=1),
            aceites_completos=True,
            profissional=profissional,
        )
        resposta = cliente.post(
            f"/api/v1/vagas/{cenario.vaga.id}/cancelamento",
            json={"motivo": "Não vou poder ir"},
            headers=cabecalho(profissional),
        )
    assert resposta.status_code == 200
    db.refresh(profissional)
    assert profissional.estado == EstadoConta.SUSPENSA
    assert resposta.json()["suspensa_ate"] is not None
    retorno = profissional.suspensa_ate.replace(tzinfo=UTC)
    assert timedelta(days=6, hours=23) < retorno - BASE < timedelta(days=7, hours=1)


def test_so_quem_participa_cancela_e_check_in_impede(cliente, db):
    cenario = _cenario(db, em_horas=10)
    estranho = criar_usuario(
        db, perfis=(TipoPerfil.CONTRATANTE,), perfil_ativo=TipoPerfil.CONTRATANTE
    )
    corpo = {"motivo": "Teste de cancelamento"}

    outro = cliente.post(
        f"/api/v1/vagas/{cenario.vaga.id}/cancelamento", json=corpo, headers=cabecalho(estranho)
    )
    cenario.vaga.checkin_em = BASE
    db.commit()
    iniciado = cliente.post(
        f"/api/v1/vagas/{cenario.vaga.id}/cancelamento",
        json=corpo,
        headers=cabecalho(cenario.contratante),
    )

    assert outro.status_code == 403
    assert iniciado.status_code == 409
    assert iniciado.json()["detail"]["codigo"] == "servico_iniciado"


# ----------------------------------------------------------------------- sanções


def test_login_de_conta_suspensa_devolve_retorno_e_motivo(cliente, db):
    usuario = criar_usuario(db, perfis=(TipoPerfil.CONTRATANTE,))
    sancoes.suspender(db, usuario, dias=7, motivo="Cancelamentos tardios", origem="advertencias")

    resposta = cliente.post(
        "/api/v1/auth/login", json={"email": usuario.email, "senha": SENHA_PADRAO}
    )

    assert resposta.status_code == 403
    detalhe = resposta.json()["detail"]
    assert detalhe["codigo"] == "conta_suspensa"
    assert detalhe["retorno_em"] is not None
    assert detalhe["motivo"] == "Cancelamentos tardios"


def test_varredura_reativa_suspensao_vencida(db):
    usuario = criar_usuario(db, perfis=(TipoPerfil.CONTRATANTE,))
    sancoes.suspender(db, usuario, dias=1, motivo="Teste", origem="admin")
    fixar_relogio(agora() + timedelta(days=2))

    resultado = varreduras.executar_varreduras(db)

    db.refresh(usuario)
    assert usuario.estado == EstadoConta.ATIVA
    assert resultado["alteracoes"]["expiracao_suspensoes"] == 1


def _avaliar(db, alvo, notas):
    """Uma avaliação visível por nota, cada uma em uma vaga e com um avaliador diferentes."""
    for nota in notas:
        cenario = criar_cenario_vaga(db, aceites_completos=True)
        db.add(
            Avaliacao(
                vaga_id=cenario.vaga.id,
                avaliador_id=cenario.profissional.id,
                avaliado_id=alvo.id,
                nota=nota,
                visivel_em=agora() - timedelta(hours=1),
            )
        )
    db.commit()


def test_nota_media_baixa_com_cinco_avaliacoes_suspende_por_30_dias(db):
    alvo = criar_usuario(db, perfis=(TipoPerfil.CONTRATANTE,))
    _avaliar(db, alvo, (1, 2, 2, 3, 2))

    sancao = sancoes.verificar_nota_baixa(db, alvo)

    assert sancao is not None
    db.refresh(alvo)
    assert alvo.estado == EstadoConta.SUSPENSA
    assert (sancao.fim_em.replace(tzinfo=UTC) - sancao.inicio_em.replace(tzinfo=UTC)).days == 30


def test_nota_baixa_com_poucas_avaliacoes_nao_suspende(db):
    alvo = criar_usuario(db, perfis=(TipoPerfil.CONTRATANTE,))
    _avaliar(db, alvo, (1, 1, 1))

    assert sancoes.verificar_nota_baixa(db, alvo) is None


def test_admin_suspende_bane_e_reativa_com_motivo(cliente, db):
    admin = _admin(db)
    alvo = criar_usuario(db, perfis=(TipoPerfil.CONTRATANTE,))
    cab = cabecalho(admin)

    suspensao = cliente.post(
        f"/api/v1/admin/usuarios/{alvo.id}/suspensao",
        json={"motivo": "Conduta inadequada", "dias": 10},
        headers=cab,
    )
    db.refresh(alvo)
    assert suspensao.status_code == 200
    assert alvo.estado == EstadoConta.SUSPENSA

    reativacao = cliente.post(f"/api/v1/admin/usuarios/{alvo.id}/reativacao", headers=cab)
    db.refresh(alvo)
    assert reativacao.status_code == 204
    assert alvo.estado == EstadoConta.ATIVA

    banimento = cliente.post(
        f"/api/v1/admin/usuarios/{alvo.id}/banimento", json={"motivo": "Fraude"}, headers=cab
    )
    db.refresh(alvo)
    assert banimento.status_code == 200
    assert alvo.estado == EstadoConta.BANIDA
    lista = cliente.get("/api/v1/admin/sancoes", headers=cab).json()
    assert [s["tipo"] for s in lista] == ["banimento"]


def test_contestacao_uma_por_caso_decidida_pelo_admin_reativa_a_conta(cliente, db):
    admin = _admin(db)
    alvo = criar_usuario(db, perfis=(TipoPerfil.CONTRATANTE,))
    sancoes.suspender(db, alvo, dias=7, motivo="Cancelamentos tardios", origem="advertencias")
    credenciais = {"email": alvo.email, "senha": SENHA_PADRAO}

    situacao = cliente.post("/api/v1/auth/sancao", json=credenciais)
    assert situacao.json()["sancao"]["pode_contestar"] is True

    contestada = cliente.post(
        "/api/v1/auth/contestacao",
        json={**credenciais, "texto": "Houve um problema de transporte comprovado."},
    )
    repetida = cliente.post(
        "/api/v1/auth/contestacao", json={**credenciais, "texto": "Segunda tentativa de contestar."}
    )
    assert contestada.status_code == 201
    assert repetida.status_code == 409

    fila = cliente.get("/api/v1/admin/contestacoes", headers=cabecalho(admin)).json()
    assert len(fila) == 1
    decisao = cliente.post(
        f"/api/v1/admin/contestacoes/{fila[0]['id']}/decisao",
        json={"aceita": True, "motivo": "Comprovante aceito"},
        headers=cabecalho(admin),
    )
    db.refresh(alvo)
    assert decisao.status_code == 200
    assert alvo.estado == EstadoConta.ATIVA


def test_contestacao_fora_do_prazo_e_recusada(cliente, db):
    alvo = criar_usuario(db, perfis=(TipoPerfil.CONTRATANTE,))
    sancoes.banir(db, alvo, motivo="Teste", origem="admin")
    fixar_relogio(agora() + timedelta(days=6))

    resposta = cliente.post(
        "/api/v1/auth/contestacao",
        json={"email": alvo.email, "senha": SENHA_PADRAO, "texto": "Contestação fora do prazo."},
    )

    assert resposta.status_code == 409
    assert resposta.json()["detail"]["codigo"] == "prazo_de_contestacao_encerrado"


def test_usuario_suspenso_fica_fora_do_match(db):
    from app.services import match

    categoria = criar_categoria(db)
    vaga = criar_vaga(db, categoria=categoria)
    profissional = criar_profissional(db, categorias=(categoria,))
    assert profissional.id in [p.id for p in match.elegiveis(db, vaga)]

    sancoes.suspender(db, profissional.usuario, dias=7, motivo="Teste", origem="admin")

    assert profissional.id not in [p.id for p in match.elegiveis(db, vaga)]


# ----------------------------------------------------------------------- nova rodada


def _vaga_com_rodada(db, categoria, estados):
    vaga = criar_vaga(db, categoria=categoria)
    for posicao, estado in enumerate(estados, start=1):
        perfil = criar_profissional(db, categorias=(categoria,))
        db.add(
            Convite(
                vaga_id=vaga.id,
                profissional_id=perfil.id,
                posicao=posicao,
                estado=estado,
                rodada=1,
            )
        )
    db.commit()
    return vaga


def test_rodada_sem_aceite_abre_a_seguinte_com_novos_candidatos(db):
    categoria = criar_categoria(db)
    vaga = _vaga_com_rodada(db, categoria, [EstadoConvite.RECUSADO, EstadoConvite.EXPIRADO])
    novos = [criar_profissional(db, categorias=(categoria,)) for _ in range(3)]

    resultado = varreduras.executar_varreduras(db)

    db.refresh(vaga)
    assert resultado["alteracoes"]["nova_rodada"] == 1
    assert vaga.rodada == 2
    convidados = db.scalars(
        select(Convite.profissional_id).where(Convite.vaga_id == vaga.id, Convite.rodada == 2)
    ).all()
    assert set(convidados) == {p.id for p in novos}
    aviso = db.scalars(select(Notificacao).where(Notificacao.tipo == "nova_rodada")).all()
    assert len(aviso) == 1


def test_rodada_com_convite_em_aberto_nao_abre_outra(db):
    categoria = criar_categoria(db)
    vaga = _vaga_com_rodada(db, categoria, [EstadoConvite.RECUSADO, EstadoConvite.SUGERIDO])
    criar_profissional(db, categorias=(categoria,))

    varreduras.executar_varreduras(db)

    db.refresh(vaga)
    assert vaga.rodada == 1


def test_sem_candidatos_a_vaga_expira_e_o_contratante_e_avisado(db):
    categoria = criar_categoria(db)
    vaga = _vaga_com_rodada(db, categoria, [EstadoConvite.RECUSADO, EstadoConvite.RECUSADO])

    varreduras.executar_varreduras(db)

    db.refresh(vaga)
    assert vaga.estado == EstadoVaga.EXPIRADA
    avisos = db.scalars(select(Notificacao).where(Notificacao.tipo == "vaga_expirada")).all()
    assert len(avisos) == 1


def test_limite_de_rodadas_expira_a_vaga(db):
    categoria = criar_categoria(db)
    vaga = _vaga_com_rodada(db, categoria, [EstadoConvite.RECUSADO])
    vaga.rodada = 3
    for convite in db.scalars(select(Convite).where(Convite.vaga_id == vaga.id)):
        convite.rodada = 3
    db.commit()
    criar_profissional(db, categorias=(categoria,))

    varreduras.executar_varreduras(db)

    db.refresh(vaga)
    assert vaga.estado == EstadoVaga.EXPIRADA


# ----------------------------------------------------------------------- agenda


def _profissional_logado(db):
    perfil = criar_profissional(db)
    return perfil, cabecalho(perfil.usuario)


def _periodo(inicio_h, fim_h):
    return {
        "inicio": (BASE + timedelta(hours=inicio_h)).isoformat(),
        "fim": (BASE + timedelta(hours=fim_h)).isoformat(),
    }


def test_criar_listar_e_remover_bloqueio(cliente, db):
    fixar_relogio(BASE)
    _, cab = _profissional_logado(db)

    criado = cliente.post(
        "/api/v1/profissional/agenda/bloqueios",
        json={**_periodo(24, 30), "motivo": "Consulta médica"},
        headers=cab,
    )
    assert criado.status_code == 201
    bloqueio_id = criado.json()["id"]

    lista = cliente.get("/api/v1/profissional/agenda/bloqueios", headers=cab).json()
    disponibilidade = cliente.get("/api/v1/profissional/disponibilidade", headers=cab).json()
    assert [b["id"] for b in lista] == [bloqueio_id]
    assert [b["motivo"] for b in disponibilidade["bloqueios"]] == ["Consulta médica"]

    removido = cliente.delete(f"/api/v1/profissional/agenda/bloqueios/{bloqueio_id}", headers=cab)
    assert removido.status_code == 204
    assert cliente.get("/api/v1/profissional/agenda/bloqueios", headers=cab).json() == []


def test_bloqueio_sobreposto_e_periodo_invalido_sao_recusados(cliente, db):
    fixar_relogio(BASE)
    _, cab = _profissional_logado(db)
    cliente.post("/api/v1/profissional/agenda/bloqueios", json=_periodo(24, 30), headers=cab)

    sobreposto = cliente.post(
        "/api/v1/profissional/agenda/bloqueios", json=_periodo(28, 34), headers=cab
    )
    invertido = cliente.post(
        "/api/v1/profissional/agenda/bloqueios", json=_periodo(50, 40), headers=cab
    )

    assert sobreposto.status_code == 409
    assert sobreposto.json()["detail"]["codigo"] == "bloqueio_sobreposto"
    assert invertido.status_code == 422


def test_so_o_dono_remove_o_bloqueio(cliente, db):
    fixar_relogio(BASE)
    _, cab = _profissional_logado(db)
    _, cab_outro = _profissional_logado(db)
    criado = cliente.post(
        "/api/v1/profissional/agenda/bloqueios", json=_periodo(24, 30), headers=cab
    )

    resposta = cliente.delete(
        f"/api/v1/profissional/agenda/bloqueios/{criado.json()['id']}", headers=cab_outro
    )

    assert resposta.status_code == 404


def test_profissional_bloqueado_fica_fora_do_match_no_periodo(db):
    from app.services import match

    categoria = criar_categoria(db)
    inicio = BASE + timedelta(days=1)
    vaga = criar_vaga(db, categoria=categoria, inicio_em=inicio, duracao_minutos=120)
    profissional = criar_profissional(db, categorias=(categoria,))
    outro = criar_profissional(db, categorias=(categoria,))
    db.add(
        BloqueioAgenda(
            profissional_id=profissional.id,
            inicio_em=inicio - timedelta(hours=1),
            fim_em=inicio + timedelta(hours=1),
        )
    )
    db.commit()

    ids = [p.id for p in match.elegiveis(db, vaga)]

    assert profissional.id not in ids
    assert outro.id in ids
