import asyncio

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.main import app
from app.services import agendador, varreduras


def _fabrica(engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def test_uma_rodada_executa_as_varreduras_registradas_em_sessao_propria(engine):
    chamadas = []
    varreduras.varredura("agendador_teste")(lambda db: chamadas.append(db) or 3)
    try:
        resultado = agendador.rodar_uma_vez(_fabrica(engine))
    finally:
        varreduras.remover_varredura("agendador_teste")

    assert resultado["alteracoes"]["agendador_teste"] == 3
    assert len(chamadas) == 1


def test_laco_repete_no_intervalo_e_sobrevive_a_uma_falha(engine):
    tentativas = []

    def instavel(db):
        tentativas.append(1)
        if len(tentativas) == 1:
            raise RuntimeError("falha de teste")
        return 1

    varreduras.varredura("agendador_instavel")(instavel)

    async def executar():
        tarefa = asyncio.create_task(agendador.laco(0.02, _fabrica(engine)))
        await asyncio.sleep(0.3)
        tarefa.cancel()
        try:
            await tarefa
        except asyncio.CancelledError:
            pass

    try:
        asyncio.run(executar())
    finally:
        varreduras.remover_varredura("agendador_instavel")

    assert len(tentativas) >= 2


def test_intervalo_padrao_depende_do_perfil(monkeypatch):
    monkeypatch.setattr(settings, "varreduras_intervalo_s", 0)
    monkeypatch.setattr(settings, "perfil", "demo")
    assert agendador.intervalo_em_segundos() == 30
    monkeypatch.setattr(settings, "perfil", "producao")
    assert agendador.intervalo_em_segundos() == 60
    monkeypatch.setattr(settings, "varreduras_intervalo_s", 5)
    assert agendador.intervalo_em_segundos() == 5


def test_app_liga_e_encerra_o_agendador_sem_erro(monkeypatch):
    monkeypatch.setattr(settings, "varreduras_automaticas", True)
    monkeypatch.setattr(settings, "varreduras_intervalo_s", 1)

    with TestClient(app) as cliente:
        assert cliente.get("/health").status_code == 200
