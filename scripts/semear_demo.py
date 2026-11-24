"""Cria contas de demonstração para desenvolvimento local e apresentações.

Uso (na raiz do repositório, com o banco já migrado):

    python scripts/semear_demo.py

O script é idempotente: contas existentes são mantidas e apenas ativadas.
Não deve ser executado em ambiente de produção.
"""

import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Categoria, Endereco, Experiencia, Vaga  # noqa: E402
from app.models.enums import (  # noqa: E402  # noqa: E402
    EstadoConta,
    PapelUsuario,
    RiscoCategoria,
    StatusAprovacao,
    TipoPerfil,
    TipoPessoa,
)
from app.models.nucleo import PerfilContratante, PerfilProfissional, Usuario  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services import match  # noqa: E402

SENHA_DEMO = "senha-segura-123"

CONTAS = [
    {
        "nome": "Ana Souza",
        "email": "ana@example.com",
        "papel": PapelUsuario.USUARIO,
        "perfis": True,
    },
    {
        "nome": "Administrador",
        "email": "admin@example.com",
        "papel": PapelUsuario.ADMIN,
        "perfis": False,
    },
]


def _obter_ou_criar(db, dados) -> Usuario:
    usuario = db.query(Usuario).filter_by(email=dados["email"]).one_or_none()
    if usuario is None:
        usuario = Usuario(
            nome=dados["nome"],
            email=dados["email"],
            senha_hash=hash_password(SENHA_DEMO),
        )
        db.add(usuario)
        db.flush()
    usuario.estado = EstadoConta.ATIVA
    usuario.papel = dados["papel"]
    return usuario


CATEGORIAS = [
    ("montagem", "Montagem de móveis", RiscoCategoria.BAIXO),
    ("limpeza", "Limpeza", RiscoCategoria.BAIXO),
    ("eletrica", "Elétrica", RiscoCategoria.ALTO),
    ("pintura", "Pintura", RiscoCategoria.MEDIO),
    ("jardinagem", "Jardinagem", RiscoCategoria.BAIXO),
]

# (nome, email, bairro, latitude, longitude, categorias, serviços concluídos)
PROFISSIONAIS = [
    (
        "Carlos Lima",
        "carlos@example.com",
        "Boa Viagem",
        -8.1190,
        -34.9040,
        ("montagem", "pintura"),
        12,
    ),
    (
        "Bruna Alves",
        "bruna@example.com",
        "Casa Forte",
        -8.0360,
        -34.9180,
        ("montagem", "limpeza"),
        8,
    ),
    (
        "Diego Rocha",
        "diego@example.com",
        "Madalena",
        -8.0520,
        -34.9010,
        ("montagem", "eletrica"),
        2,
    ),
    (
        "Elisa Prado",
        "elisa@example.com",
        "Graças",
        -8.0420,
        -34.8980,
        ("montagem", "jardinagem"),
        20,
    ),
]

# (título, categoria, valor, horas, dias à frente, bairro, latitude, longitude)
VAGAS = [
    ("Montagem de guarda-roupa", "montagem", 180, 3, 1, "Boa Vista", -8.0630, -34.8760),
    ("Limpeza pós-obra", "limpeza", 240, 4, 1, "Derby", -8.0540, -34.8990),
    ("Pintura de sala", "pintura", 320, 5, 2, "Espinheiro", -8.0400, -34.8960),
    ("Troca de fiação", "eletrica", 280, 3, 2, "Torre", -8.0480, -34.9120),
]


def _usuario(db, nome, email, ativo=True):
    usuario = db.query(Usuario).filter_by(email=email).one_or_none()
    if usuario is None:
        usuario = Usuario(nome=nome, email=email, senha_hash=hash_password(SENHA_DEMO))
        db.add(usuario)
        db.flush()
    usuario.estado = EstadoConta.ATIVA
    return usuario


def semear_marketplace(db) -> None:
    """Categorias, profissionais e vagas de exemplo; só cria o que ainda não existe."""
    por_slug = {}
    for slug, nome, risco in CATEGORIAS:
        categoria = db.query(Categoria).filter_by(slug=slug).one_or_none()
        if categoria is None:
            categoria = Categoria(slug=slug, nome=nome, risco=risco)
            db.add(categoria)
            db.flush()
        por_slug[slug] = categoria

    for indice, (nome, email, bairro, lat, lon, slugs, concluidos) in enumerate(PROFISSIONAIS):
        usuario = _usuario(db, nome, email)
        if usuario.perfil_profissional is None:
            endereco = Endereco(bairro=bairro, latitude=lat, longitude=lon)
            db.add(endereco)
            db.flush()
            db.add(
                PerfilProfissional(
                    usuario_id=usuario.id,
                    cpf=f"9000000000{indice}",
                    data_nascimento=date(1992, 3, 10),
                    telefone="81999991000",
                    endereco_id=endereco.id,
                    raio_km=15,
                    servicos_concluidos=concluidos,
                    verificacao=StatusAprovacao.APROVADO,
                    experiencia_aprovada=StatusAprovacao.APROVADO,
                    categorias=[por_slug[s] for s in slugs],
                )
            )
        usuario.perfil_ativo = TipoPerfil.PROFISSIONAL

    maria = _usuario(db, "Maria Contratante", "maria@example.com")
    if maria.perfil_contratante is None:
        db.add(
            PerfilContratante(
                usuario_id=maria.id,
                tipo_pessoa=TipoPessoa.FISICA,
                documento="80000000001",
                telefone="81988881000",
                verificacao=StatusAprovacao.APROVADO,
            )
        )
        db.flush()
    maria.perfil_ativo = TipoPerfil.CONTRATANTE
    db.flush()
    db.refresh(maria)

    # A Ana também tem endereço e categorias, para receber convites e ver o mapa.
    ana = db.query(Usuario).filter_by(email="ana@example.com").one()
    perfil = ana.perfil_profissional
    if perfil is not None and perfil.endereco_id is None:
        endereco = Endereco(bairro="Boa Vista", latitude=-8.0600, longitude=-34.8800)
        db.add(endereco)
        db.flush()
        perfil.endereco_id = endereco.id
        perfil.raio_km = 15
        perfil.servicos_concluidos = 6
        perfil.verificacao = StatusAprovacao.APROVADO
        perfil.experiencia_aprovada = StatusAprovacao.APROVADO
        perfil.categorias = list(por_slug.values())
        for categoria in por_slug.values():
            db.add(
                Experiencia(
                    profissional_id=perfil.id,
                    categoria_id=categoria.id,
                    descricao=f"Serviços de {categoria.nome.lower()} em residências e comércios.",
                    status=StatusAprovacao.APROVADO,
                )
            )
    if ana.perfil_contratante is not None:
        ana.perfil_contratante.verificacao = StatusAprovacao.APROVADO

    if db.query(Vaga).filter_by(contratante_id=maria.perfil_contratante.id).count() == 0:
        base = datetime.now(UTC)
        for titulo, slug, valor, horas, dias, bairro, lat, lon in VAGAS:
            endereco = Endereco(bairro=bairro, latitude=lat, longitude=lon)
            db.add(endereco)
            db.flush()
            vaga = Vaga(
                contratante_id=maria.perfil_contratante.id,
                categoria_id=por_slug[slug].id,
                endereco_id=endereco.id,
                titulo=titulo,
                descricao=f"{titulo}. Material fornecido pelo contratante.",
                inicio_em=base + timedelta(days=dias, hours=2),
                duracao_minutos=horas * 60,
                valor_inicial=Decimal(valor),
                prazo_em=base + timedelta(days=3),
            )
            db.add(vaga)
            db.flush()
            match.gerar_rodada(db, vaga)
    db.commit()


def main() -> int:
    if getattr(settings, "ambiente", "desenvolvimento") == "producao":
        print("Recusado: o ambiente configurado é de produção.")
        return 1

    db = SessionLocal()
    try:
        for dados in CONTAS:
            usuario = _obter_ou_criar(db, dados)
            if dados["perfis"]:
                if usuario.perfil_profissional is None:
                    db.add(
                        PerfilProfissional(
                            usuario_id=usuario.id,
                            cpf="12345678901",
                            data_nascimento=date(1995, 5, 5),
                            telefone="81999990000",
                        )
                    )
                if usuario.perfil_contratante is None:
                    db.add(
                        PerfilContratante(
                            usuario_id=usuario.id,
                            tipo_pessoa=TipoPessoa.FISICA,
                            telefone="81999990000",
                        )
                    )
                usuario.perfil_ativo = TipoPerfil.PROFISSIONAL
        db.commit()
        semear_marketplace(db)
    finally:
        db.close()

    print(f"Contas de demonstração prontas (senha: {SENHA_DEMO}):")
    for dados in CONTAS:
        print(f"  {dados['email']} ({dados['papel'].value})")
    print("  maria@example.com (contratante), carlos/bruna/diego/elisa@example.com (profissionais)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
