import json
import logging

from fastapi import APIRouter, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from app.config import Settings
from app.erros import (
    Conflict,
    ErroDominio,
    Forbidden,
    NotFound,
    RuleViolation,
    handler_erro_dominio,
    handler_erro_inesperado,
    handler_validacao_pydantic,
)
from app.main import app
from tests.fabricas import cabecalho, criar_usuario


class _PayloadTeste(BaseModel):
    nome: str = Field(min_length=3)
    idade: int = Field(gt=0)


def _criar_app_isolado() -> FastAPI:
    teste_app = FastAPI()
    teste_app.add_exception_handler(ErroDominio, handler_erro_dominio)
    teste_app.add_exception_handler(RequestValidationError, handler_validacao_pydantic)
    teste_app.add_exception_handler(Exception, handler_erro_inesperado)

    router = APIRouter()

    @router.get("/not-found")
    def rota_not_found():
        raise NotFound("Vaga não encontrada.", details={"vaga_id": 99})

    @router.get("/conflict")
    def rota_conflict():
        raise Conflict("Já existe uma proposta ativa.")

    @router.get("/forbidden")
    def rota_forbidden():
        raise Forbidden("Perfil sem permissão para esta ação.")

    @router.get("/rule-violation")
    def rota_rule_violation():
        raise RuleViolation("O valor não pode ser menor que o mínimo.", details={"minimo": 50})

    @router.get("/erro-500")
    def rota_erro_500():
        raise RuntimeError("Falha inesperada")

    @router.post("/validar")
    def rota_validar(body: _PayloadTeste):
        return body.model_dump()

    teste_app.include_router(router)
    return teste_app


def test_excecoes_dominio_devolvem_formato_unico():
    cliente = TestClient(_criar_app_isolado(), raise_server_exceptions=False)

    casos = [
        ("/not-found", 404, "nao_encontrado", "Vaga não encontrada.", {"vaga_id": 99}),
        ("/conflict", 409, "conflito", "Já existe uma proposta ativa.", None),
        ("/forbidden", 403, "acesso_negado", "Perfil sem permissão para esta ação.", None),
        (
            "/rule-violation",
            422,
            "violacao_regra",
            "O valor não pode ser menor que o mínimo.",
            {"minimo": 50},
        ),
        ("/erro-500", 500, "erro_interno", "Ocorreu um erro interno inesperado.", None),
    ]

    for rota, status_esperado, code_esperado, msg_esperada, details_esperado in casos:
        resp = cliente.get(rota)
        assert resp.status_code == status_esperado
        corpo = resp.json()
        assert corpo["code"] == code_esperado
        assert corpo["message"] == msg_esperada
        assert corpo["details"] == details_esperado


def test_validacao_pydantic_traduzida_para_pt_br():
    cliente = TestClient(_criar_app_isolado())

    resp = cliente.post("/validar", json={"nome": "ab", "idade": -5})

    assert resp.status_code == 422
    corpo = resp.json()
    assert corpo["code"] == "erro_validacao"
    assert "Erro de validação:" in corpo["message"]
    erros = corpo["details"]["errors"]
    assert erros == [
        {"field": "nome", "message": "Deve ter pelo menos 3 caracteres."},
        {"field": "idade", "message": "Deve ser maior que 0."},
    ]


def test_validacao_campo_obrigatorio_em_pt_br():
    cliente = TestClient(_criar_app_isolado())

    resp = cliente.post("/validar", json={})

    assert resp.status_code == 422
    erros = resp.json()["details"]["errors"]
    assert all(item["message"] == "Campo obrigatório." for item in erros)


def test_cors_liberado_para_origem_do_front():
    cliente = TestClient(app)

    resp = cliente.options(
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_config_aceita_cors_origins_em_json_ou_virgula():
    via_virgula = Settings(cors_origins="http://localhost:3000, https://app.freelai.com")
    via_json = Settings(cors_origins=["http://localhost:3000"])

    assert via_virgula.cors_origins == ["http://localhost:3000", "https://app.freelai.com"]
    assert via_json.cors_origins == ["http://localhost:3000"]


def test_middleware_log_estruturado_com_e_sem_usuario(cliente, db):
    usuario = criar_usuario(db)
    logger_req = logging.getLogger("freelai.requisicoes")
    registros: list[str] = []

    class _Coletor(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            registros.append(self.format(record))

    coletor = _Coletor()
    coletor.setFormatter(logging.Formatter("%(message)s"))
    logger_req.addHandler(coletor)
    try:
        r_anon = cliente.get("/health")
        r_auth = cliente.get("/api/v1/vagas", headers=cabecalho(usuario))
    finally:
        logger_req.removeHandler(coletor)

    assert r_anon.status_code == 200
    assert len(registros) == 2

    linhas = [json.loads(msg) for msg in registros]

    assert linhas[0]["method"] == "GET"
    assert linhas[0]["path"] == "/health"
    assert linhas[0]["status"] == 200
    assert isinstance(linhas[0]["duration_ms"], float)
    assert linhas[0]["user_id"] is None

    assert linhas[1]["method"] == "GET"
    assert linhas[1]["path"] == "/api/v1/vagas"
    assert linhas[1]["status"] == r_auth.status_code
    assert isinstance(linhas[1]["duration_ms"], float)
    assert linhas[1]["user_id"] == usuario.id
