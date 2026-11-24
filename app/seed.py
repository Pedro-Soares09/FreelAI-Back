"""Dados fictícios de demonstração. Execute com: python -m app.seed"""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import (
    Categoria,
    ChecklistItem,
    Endereco,
    EpiCategoria,
    EpiProfissional,
    EstadoConta,
    EstadoVaga,
    Experiencia,
    PapelUsuario,
    PerfilContratante,
    PerfilProfissional,
    Qualificacao,
    RiscoCategoria,
    StatusAprovacao,
    TipoPerfil,
    TipoPessoa,
    TipoQualificacao,
    TipoVaga,
    Usuario,
    Vaga,
)
from app.security import hash_password
from app.services.configuracao import semear_configuracoes
from app.tempo import agora

SENHA_SEED = "FreelAI@2026!"

# slug, nome, risco, exige qualificação, exige EPI, EPIs, checklist (texto, crítico)
CATEGORIAS = [
    (
        "montagem",
        "Montagem de móveis",
        RiscoCategoria.BAIXO,
        False,
        False,
        ["Luvas de proteção", "Óculos de proteção"],
        [("Área de trabalho organizada?", False), ("Ferramentas em boas condições?", True)],
    ),
    (
        "limpeza",
        "Limpeza",
        RiscoCategoria.BAIXO,
        False,
        False,
        ["Luvas de proteção", "Calçado antiderrapante"],
        [("Produtos identificados corretamente?", True), ("Ambiente ventilado?", True)],
    ),
    (
        "eletrica",
        "Elétrica",
        RiscoCategoria.ALTO,
        True,
        True,
        ["Luvas isolantes", "Óculos de proteção", "Capacete de segurança", "Calçado de segurança"],
        [
            ("Circuito desenergizado antes do serviço?", True),
            ("Ferramentas isoladas e adequadas?", True),
        ],
    ),
    (
        "pintura",
        "Pintura",
        RiscoCategoria.MEDIO,
        False,
        True,
        ["Máscara respiratória", "Óculos de proteção", "Luvas de proteção"],
        [
            ("Ambiente possui ventilação adequada?", True),
            ("Escada posicionada com segurança?", True),
        ],
    ),
    (
        "jardinagem",
        "Jardinagem",
        RiscoCategoria.BAIXO,
        False,
        False,
        ["Luvas de proteção", "Botas de segurança"],
        [("Área livre de obstáculos?", False), ("Ferramentas em boas condições?", True)],
    ),
]

# nome, bairro, latitude, longitude, categoria, serviços concluídos, Pro, pendente
# Cada grupo de quatro contém um Novo, um Pro, um pendente e um verificado.
PROFISSIONAIS = [
    ("Carlos Lima", "Boa Vista", -8.0610, -34.8800, "montagem", 0, False, False),
    ("Bruna Alves", "Boa Viagem", -8.1190, -34.9040, "montagem", 12, True, False),
    ("Diego Rocha", "Casa Forte", -8.0340, -34.9180, "montagem", 1, False, True),
    ("Elisa Prado", "Várzea", -8.0500, -34.9600, "montagem", 8, False, False),
    ("Felipe Santos", "Pina", -8.0840, -34.8890, "limpeza", 0, False, False),
    ("Gabriela Melo", "Madalena", -8.0520, -34.9020, "limpeza", 14, True, False),
    ("Heitor Costa", "Graças", -8.0430, -34.8950, "limpeza", 0, False, True),
    ("Isabela Freitas", "Cordeiro", -8.0600, -34.9350, "limpeza", 7, False, False),
    ("João Pedro", "Derby", -8.0560, -34.8980, "eletrica", 0, False, False),
    ("Karen Oliveira", "Torre", -8.0480, -34.9120, "eletrica", 11, True, False),
    ("Lucas Barros", "Imbiribeira", -8.1040, -34.9110, "eletrica", 0, False, True),
    ("Marina Lopes", "Dois Irmãos", -8.0050, -34.9400, "eletrica", 9, False, False),
    ("Nicolas Ribeiro", "Boa Viagem", -8.1160, -34.9000, "pintura", 0, False, False),
    ("Olívia Martins", "Casa Forte", -8.0360, -34.9160, "pintura", 13, True, False),
    ("Paulo Henrique", "Várzea", -8.0520, -34.9580, "pintura", 0, False, True),
    ("Queila Nunes", "Espinheiro", -8.0400, -34.9010, "pintura", 6, False, False),
    ("Rafael Gomes", "Afogados", -8.0750, -34.9100, "jardinagem", 0, False, False),
    ("Sabrina Castro", "Tamarineira", -8.0260, -34.9080, "jardinagem", 10, True, False),
    ("Tiago Almeida", "Iputinga", -8.0400, -34.9500, "jardinagem", 0, False, True),
    ("Vitória Souza", "Encruzilhada", -8.0300, -34.8900, "jardinagem", 8, False, False),
]

# nome, tipo pessoa, telefone, razão social (opcional)
CONTRATANTES = [
    ("Mariana Ferreira", TipoPessoa.FISICA, "81980001001", None),
    ("Roberto Nascimento", TipoPessoa.FISICA, "81980001002", None),
    ("Comercial Recife Ltda.", TipoPessoa.JURIDICA, "81980001003", "Comercial Recife Ltda."),
    ("Patrícia Andrade", TipoPessoa.FISICA, "81980001004", None),
    ("Eduardo Cavalcanti", TipoPessoa.FISICA, "81980001005", None),
]

# título, categoria, índice contratante, bairro, lat, lon, valor, estado, email profissional, dias
VAGAS = [
    (
        "Montagem de guarda-roupa",
        "montagem",
        0,
        "Boa Vista",
        -8.0630,
        -34.8760,
        "180.00",
        EstadoVaga.PUBLICADA,
        None,
        2,
    ),
    (
        "Limpeza pós-obra",
        "limpeza",
        1,
        "Boa Viagem",
        -8.1190,
        -34.9040,
        "240.00",
        EstadoVaga.AGUARDANDO_ACEITE,
        "profissional06@freelai.local",
        3,
    ),
    (
        "Instalação elétrica residencial",
        "eletrica",
        2,
        "Casa Forte",
        -8.0370,
        -34.9180,
        "350.00",
        EstadoVaga.AGUARDANDO_PAGAMENTO,
        "profissional10@freelai.local",
        4,
    ),
    (
        "Pintura de sala",
        "pintura",
        3,
        "Várzea",
        -8.0500,
        -34.9600,
        "320.00",
        EstadoVaga.EM_EXECUCAO,
        "profissional14@freelai.local",
        -1,
    ),
    (
        "Manutenção de jardim",
        "jardinagem",
        4,
        "Pina",
        -8.0860,
        -34.8870,
        "150.00",
        EstadoVaga.CONCLUIDA,
        "profissional18@freelai.local",
        -5,
    ),
    (
        "Montagem de móveis de escritório",
        "montagem",
        2,
        "Torre",
        -8.0480,
        -34.9120,
        "420.00",
        EstadoVaga.CANCELADA,
        None,
        5,
    ),
]


def _cpf(numero_base: int) -> str:
    base = f"{numero_base:09d}"

    def digito(digitos: str, pesos: range) -> str:
        soma = sum(int(n) * peso for n, peso in zip(digitos, pesos, strict=True))
        resto = soma % 11
        return "0" if resto < 2 else str(11 - resto)

    primeiro = digito(base, range(10, 1, -1))
    return base + primeiro + digito(base + primeiro, range(11, 1, -1))


def _usuario(db: Session, nome: str, email: str, papel=PapelUsuario.USUARIO) -> Usuario:
    usuario = db.scalar(select(Usuario).where(Usuario.email == email))
    if usuario is None:
        usuario = Usuario(nome=nome, email=email, senha_hash=hash_password(SENHA_SEED))
        db.add(usuario)
        db.flush()
    usuario.nome = nome
    usuario.senha_hash = hash_password(SENHA_SEED)
    usuario.estado = EstadoConta.ATIVA
    usuario.papel = papel
    return usuario


def _endereco(db: Session, atual: Endereco | None, bairro: str, lat: float, lon: float) -> Endereco:
    if atual is None:
        atual = Endereco(bairro=bairro, latitude=lat, longitude=lon)
        db.add(atual)
    else:
        atual.bairro, atual.latitude, atual.longitude = bairro, lat, lon
        atual.cidade, atual.uf = "Recife", "PE"
    db.flush()
    return atual


def semear_categorias(db: Session) -> dict[str, Categoria]:
    resultado = {}
    for slug, nome, risco, exige_qualificacao, exige_epi, epis, checklist in CATEGORIAS:
        categoria = db.scalar(select(Categoria).where(Categoria.slug == slug))
        if categoria is None:
            categoria = Categoria(slug=slug, nome=nome, risco=risco)
            db.add(categoria)
            db.flush()
        categoria.nome = nome
        categoria.risco = risco
        categoria.exige_qualificacao = exige_qualificacao
        categoria.exige_epi = exige_epi
        resultado[slug] = categoria

        existentes = set(
            db.scalars(
                select(EpiCategoria.descricao).where(EpiCategoria.categoria_id == categoria.id)
            ).all()
        )
        for descricao in epis:
            if descricao not in existentes:
                db.add(EpiCategoria(categoria_id=categoria.id, descricao=descricao))

        itens = {
            i.texto: i
            for i in db.scalars(
                select(ChecklistItem).where(ChecklistItem.categoria_id == categoria.id)
            ).all()
        }
        for texto, critico in checklist:
            if texto in itens:
                itens[texto].critico = critico
            else:
                db.add(ChecklistItem(categoria_id=categoria.id, texto=texto, critico=critico))
    db.flush()
    return resultado


def semear_profissionais(
    db: Session, categorias: dict[str, Categoria]
) -> dict[str, PerfilProfissional]:
    resultado = {}
    for indice, (nome, bairro, lat, lon, slug, concluidos, pro, pendente) in enumerate(
        PROFISSIONAIS, 1
    ):
        email = f"profissional{indice:02d}@freelai.local"
        usuario = _usuario(db, nome, email)
        usuario.perfil_ativo = TipoPerfil.PROFISSIONAL
        categoria = categorias[slug]
        status = StatusAprovacao.PENDENTE if pendente else StatusAprovacao.APROVADO
        if pendente:
            usuario.estado = EstadoConta.PENDENTE

        perfil = db.scalar(
            select(PerfilProfissional).where(PerfilProfissional.usuario_id == usuario.id)
        )
        if perfil is None:
            perfil = PerfilProfissional(
                usuario_id=usuario.id,
                cpf=_cpf(900000000 + indice),
                data_nascimento=date(1990 + indice % 12, 3, 10),
                telefone=f"8199000{indice:04d}",
            )
            db.add(perfil)
            db.flush()
        perfil.data_nascimento = date(1990 + indice % 12, 3, 10)
        perfil.telefone = f"8199000{indice:04d}"
        perfil.raio_km = 15
        perfil.servicos_concluidos = concluidos
        perfil.pro = pro
        perfil.verificacao = status
        perfil.verificacao_motivo = "Aguardando análise dos documentos." if pendente else None
        perfil.experiencia_aprovada = status
        perfil.comunicacao, perfil.agilidade, perfil.organizacao = 4, 4, 5
        perfil.endereco = _endereco(db, perfil.endereco, bairro, lat, lon)
        perfil.categorias = [categoria]

        descricao = f"Experiência demonstrativa em {categoria.nome}."
        experiencia = db.scalar(
            select(Experiencia).where(
                Experiencia.profissional_id == perfil.id,
                Experiencia.categoria_id == categoria.id,
                Experiencia.descricao == descricao,
            )
        )
        if experiencia is None:
            experiencia = Experiencia(
                profissional_id=perfil.id, categoria_id=categoria.id, descricao=descricao
            )
            db.add(experiencia)
        experiencia.referencia_nome = "Referência fictícia"
        experiencia.referencia_contato = "referencia@freelai.local"
        experiencia.status = status

        if categoria.exige_qualificacao:
            qualificacao = db.scalar(
                select(Qualificacao).where(
                    Qualificacao.profissional_id == perfil.id,
                    Qualificacao.categoria_id == categoria.id,
                )
            )
            if qualificacao is None:
                qualificacao = Qualificacao(
                    profissional_id=perfil.id,
                    categoria_id=categoria.id,
                    tipo=TipoQualificacao.CERTIFICADO,
                )
                db.add(qualificacao)
            qualificacao.referencia = "Certificado fictício de demonstração"
            qualificacao.arquivo_url = f"seed/qualificacoes/{indice}.pdf"
            qualificacao.status = status

        if categoria.exige_epi:
            epi = db.scalar(
                select(EpiProfissional).where(
                    EpiProfissional.profissional_id == perfil.id,
                    EpiProfissional.categoria_id == categoria.id,
                )
            )
            if epi is None:
                epi = EpiProfissional(
                    profissional_id=perfil.id,
                    categoria_id=categoria.id,
                    arquivo_url=f"seed/epis/{indice}.jpg",
                )
                db.add(epi)
            epi.arquivo_url = f"seed/epis/{indice}.jpg"
            epi.status = status
        resultado[email] = perfil
    db.flush()
    return resultado


def semear_contratantes(db: Session) -> list[PerfilContratante]:
    resultado = []
    for indice, (nome, tipo, telefone, razao) in enumerate(CONTRATANTES, 1):
        usuario = _usuario(db, nome, f"contratante{indice:02d}@freelai.local")
        usuario.perfil_ativo = TipoPerfil.CONTRATANTE
        perfil = db.scalar(
            select(PerfilContratante).where(PerfilContratante.usuario_id == usuario.id)
        )
        if perfil is None:
            perfil = PerfilContratante(usuario_id=usuario.id, tipo_pessoa=tipo, telefone=telefone)
            db.add(perfil)
        perfil.tipo_pessoa = tipo
        perfil.telefone = telefone
        perfil.verificacao = StatusAprovacao.APROVADO
        perfil.documento = _cpf(910000000 + indice) if tipo == TipoPessoa.FISICA else None
        perfil.razao_social = razao
        resultado.append(perfil)
    db.flush()
    return resultado


def semear_vagas(
    db: Session,
    categorias: dict[str, Categoria],
    contratantes: list[PerfilContratante],
    profissionais: dict[str, PerfilProfissional],
) -> None:
    instante = agora()
    for (
        titulo,
        slug,
        indice_contratante,
        bairro,
        lat,
        lon,
        valor,
        estado,
        email_prof,
        dias,
    ) in VAGAS:
        contratante = contratantes[indice_contratante]
        vaga = db.scalar(
            select(Vaga).where(Vaga.contratante_id == contratante.id, Vaga.titulo == titulo)
        )
        endereco_atual = db.get(Endereco, vaga.endereco_id) if vaga and vaga.endereco_id else None
        endereco = _endereco(db, endereco_atual, bairro, lat, lon)
        if vaga is None:
            vaga = Vaga(
                contratante_id=contratante.id,
                categoria_id=categorias[slug].id,
                endereco_id=endereco.id,
                titulo=titulo,
                descricao=f"Vaga fictícia para demonstração: {titulo}.",
                tipo=TipoVaga.AVULSO,
                inicio_em=instante,
                duracao_minutos=180,
                valor_inicial=Decimal(valor),
            )
            db.add(vaga)
        vaga.categoria_id = categorias[slug].id
        vaga.endereco_id = endereco.id
        vaga.descricao = f"Vaga fictícia para demonstração: {titulo}."
        vaga.inicio_em = instante + timedelta(days=dias)
        vaga.duracao_minutos = 180
        vaga.valor_inicial = Decimal(valor)
        vaga.valor_fixado = Decimal(valor) if email_prof else None
        vaga.estado = estado
        vaga.rodada = 1
        vaga.prazo_em = instante + timedelta(hours=12)
        vaga.profissional_id = profissionais[email_prof].id if email_prof else None
    db.flush()


def semear(db: Session) -> None:
    semear_configuracoes(db)
    _usuario(db, "Administrador FreelAI", "admin@freelai.local", PapelUsuario.ADMIN)
    categorias = semear_categorias(db)
    profissionais = semear_profissionais(db, categorias)
    contratantes = semear_contratantes(db)
    semear_vagas(db, categorias, contratantes, profissionais)
    db.commit()


def main() -> int:
    if settings.ambiente == "producao":
        print("Seed bloqueado: não execute dados fictícios em produção.")
        return 1
    db = SessionLocal()
    try:
        semear(db)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    print("Seed do FreelAI concluído.")
    print(f"Senha de todas as contas: {SENHA_SEED}")
    print("Admin: admin@freelai.local")
    print("Profissionais: profissional01@freelai.local até profissional20@freelai.local")
    print("Contratantes: contratante01@freelai.local até contratante05@freelai.local")
    print("Categorias: montagem, limpeza, eletrica, pintura e jardinagem")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
