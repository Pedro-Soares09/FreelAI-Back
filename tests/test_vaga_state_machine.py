from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from fastapi import HTTPException
from app.models import (
    Categoria,
    Endereco,
    EstadoConta,
    EstadoVaga,
    PapelUsuario,
    PerfilContratante,
    RiscoCategoria,
    StatusAprovacao,
    TipoPessoa,
    TipoVaga,
    Usuario,
    Vaga,
)
from app.services import vagas as vagas_servico
from app.services import varreduras


def _criar_dependencias(db):
    agora_utc = datetime.now(timezone.utc)
    usuario = Usuario(
        nome="Contratante Teste",
        email="contratante.us23@example.com",
        senha_hash="hash_teste_123",
        estado=EstadoConta.ATIVA,
        papel=PapelUsuario.USUARIO,
        criado_em=agora_utc,
        atualizado_em=agora_utc,
    )
    categoria = Categoria(
        slug="atendimento-us23",
        nome="Atendimento",
        risco=RiscoCategoria.BAIXO,
        exige_qualificacao=False,
        exige_epi=False,
    )
    endereco = Endereco(
        bairro="Boa Vista",
        cidade="Recife",
        uf="PE",
        latitude=-8.0631,
        longitude=-34.8711,
    )
    db.add_all([usuario, categoria, endereco])
    db.flush()

    perfil_contratante = PerfilContratante(
        usuario_id=usuario.id,
        tipo_pessoa=TipoPessoa.FISICA,
        telefone="81999999999",
        verificacao=StatusAprovacao.APROVADO,
        criado_em=agora_utc,
        atualizado_em=agora_utc,
    )
    db.add(perfil_contratante)
    db.flush()
    return perfil_contratante.id, categoria.id, endereco.id


def _nova_vaga(contratante_id, categoria_id, endereco_id, estado=EstadoVaga.PUBLICADA, inicio_em=None, prazo_em=None):
    agora_utc = datetime.now(timezone.utc)
    inicio = inicio_em or (agora_utc + timedelta(hours=24))
    return Vaga(
        contratante_id=contratante_id,
        categoria_id=categoria_id,
        endereco_id=endereco_id,
        titulo="Vaga teste US23",
        descricao="Descricao de teste para maquina de estados",
        tipo=TipoVaga.AVULSO,
        inicio_em=inicio,
        duracao_minutos=120,
        valor_inicial=Decimal("150.00"),
        estado=estado,
        rodada=1,
        prazo_em=prazo_em,
        criado_em=agora_utc,
        atualizado_em=agora_utc,
    )


def test_transicao_valida_e_bloqueio_invalida():
    vaga = _nova_vaga(1, 1, 1, estado=EstadoVaga.PUBLICADA)
    vagas_servico.transicionar(
        vaga,
        vagas_servico.EventoVaga.INICIAR_CONTRATACAO,
        motivo="escolha_profissional",
    )
    assert vaga.estado == EstadoVaga.AGUARDANDO_ACEITE

    with pytest.raises(HTTPException) as exc:
        vagas_servico.transicionar(
            vaga,
            vagas_servico.EventoVaga.CONCLUIR_SERVICO,
            motivo="transicao_invalida",
        )
    assert exc.value.status_code == 409


def test_prazo_publicacao_flx09_respeita_12h_e_inicio_em(db):
    referencia = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    inicio_distante = referencia + timedelta(hours=36)
    inicio_proximo = referencia + timedelta(hours=4)

    prazo_12h = vagas_servico.calcular_prazo_publicacao(db, inicio_distante, referencia=referencia)
    prazo_4h = vagas_servico.calcular_prazo_publicacao(db, inicio_proximo, referencia=referencia)

    assert prazo_12h == referencia + timedelta(hours=12)
    assert prazo_4h == inicio_proximo


def test_varredura_expira_vaga_com_prazo_vencido(db):
    contratante_id, categoria_id, endereco_id = _criar_dependencias(db)
    vencido = datetime.now(timezone.utc) - timedelta(minutes=10)
    vaga = _nova_vaga(
        contratante_id,
        categoria_id,
        endereco_id,
        estado=EstadoVaga.PUBLICADA,
        inicio_em=datetime.now(timezone.utc) + timedelta(hours=6),
        prazo_em=vencido,
    )
    db.add(vaga)
    db.commit()
    db.refresh(vaga)

    varreduras.executar_varreduras(db)
    db.refresh(vaga)
    assert vaga.estado == EstadoVaga.EXPIRADA
