"""Funções auxiliares para montar dados de teste."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import (
    Categoria,
    Endereco,
    EstadoConta,
    EstadoVaga,
    PapelUsuario,
    PerfilContratante,
    PerfilProfissional,
    RiscoCategoria,
    StatusAprovacao,
    TipoPerfil,
    TipoPessoa,
    Usuario,
    Vaga,
)
from app.security import create_access_token, hash_password
from app.tempo import agora

# Coordenadas de referência em Recife, usadas como centro das vagas de teste.
LATITUDE_PADRAO = -8.0505
LONGITUDE_PADRAO = -34.9500

SENHA_PADRAO = "senha-segura-123"
_contador = {"n": 0}


def _proximo() -> int:
    _contador["n"] += 1
    return _contador["n"]


def criar_usuario(
    db: Session,
    *,
    email: str | None = None,
    estado: EstadoConta = EstadoConta.ATIVA,
    papel: PapelUsuario = PapelUsuario.USUARIO,
    perfis: tuple[TipoPerfil, ...] = (),
    perfil_ativo: TipoPerfil | None = None,
    verificado: bool = False,
) -> Usuario:
    n = _proximo()
    usuario = Usuario(
        nome=f"Usuário {n}",
        email=email or f"usuario{n}@example.com",
        senha_hash=hash_password(SENHA_PADRAO),
        estado=estado,
        papel=papel,
        perfil_ativo=perfil_ativo,
    )
    db.add(usuario)
    db.flush()
    aprovacao = StatusAprovacao.APROVADO if verificado else StatusAprovacao.PENDENTE
    if TipoPerfil.PROFISSIONAL in perfis:
        db.add(
            PerfilProfissional(
                usuario_id=usuario.id,
                cpf=f"{n:011d}",
                data_nascimento=date(1990, 1, 1),
                telefone="81999990000",
                verificacao=aprovacao,
            )
        )
    if TipoPerfil.CONTRATANTE in perfis:
        db.add(
            PerfilContratante(
                usuario_id=usuario.id,
                tipo_pessoa=TipoPessoa.FISICA,
                documento=f"{n:011d}",
                telefone="81988880000",
                verificacao=aprovacao,
            )
        )
    db.commit()
    db.refresh(usuario)
    return usuario


def cabecalho(usuario: Usuario) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(usuario.id))}"}


def criar_categoria(
    db: Session,
    *,
    risco: RiscoCategoria = RiscoCategoria.BAIXO,
    exige_qualificacao: bool = False,
    exige_epi: bool = False,
) -> Categoria:
    n = _proximo()
    categoria = Categoria(
        slug=f"categoria-{n}",
        nome=f"Categoria {n}",
        risco=risco,
        exige_qualificacao=exige_qualificacao,
        exige_epi=exige_epi,
    )
    db.add(categoria)
    db.commit()
    db.refresh(categoria)
    return categoria


def criar_endereco(
    db: Session,
    *,
    latitude: float = LATITUDE_PADRAO,
    longitude: float = LONGITUDE_PADRAO,
) -> Endereco:
    endereco = Endereco(bairro="Várzea", latitude=latitude, longitude=longitude)
    db.add(endereco)
    db.commit()
    db.refresh(endereco)
    return endereco


def criar_profissional(
    db: Session,
    *,
    categorias: tuple[Categoria, ...] = (),
    latitude: float | None = LATITUDE_PADRAO,
    longitude: float | None = LONGITUDE_PADRAO,
    raio_km: int = 10,
    estado: EstadoConta = EstadoConta.ATIVA,
    verificado: bool = True,
    servicos_concluidos: int = 10,
    experiencia_aprovada: StatusAprovacao = StatusAprovacao.PENDENTE,
) -> PerfilProfissional:
    """Perfil profissional pronto para o match; sem coordenadas, fica sem endereço."""
    usuario = criar_usuario(
        db,
        estado=estado,
        perfis=(TipoPerfil.PROFISSIONAL,),
        perfil_ativo=TipoPerfil.PROFISSIONAL,
        verificado=verificado,
    )
    perfil = usuario.perfil_profissional
    if latitude is not None and longitude is not None:
        perfil.endereco_id = criar_endereco(db, latitude=latitude, longitude=longitude).id
    perfil.raio_km = raio_km
    perfil.servicos_concluidos = servicos_concluidos
    perfil.experiencia_aprovada = experiencia_aprovada
    perfil.categorias = list(categorias)
    db.commit()
    db.refresh(perfil)
    return perfil


@dataclass
class CenarioVaga:
    """Vaga com contratante e profissional de contas distintas, pronta para o pagamento."""

    vaga: Vaga
    contratante: Usuario
    profissional: Usuario


def criar_vaga(
    db: Session,
    *,
    categoria: Categoria,
    contratante: PerfilContratante | None = None,
    latitude: float = LATITUDE_PADRAO,
    longitude: float = LONGITUDE_PADRAO,
    inicio_em: datetime | None = None,
    duracao_minutos: int = 120,
    valor_inicial: Decimal = Decimal("100.00"),
    valor_fixado: Decimal | None = None,
    estado: EstadoVaga = EstadoVaga.PUBLICADA,
    profissional: PerfilProfissional | None = None,
) -> Vaga:
    if contratante is None:
        contratante = criar_usuario(
            db, perfis=(TipoPerfil.CONTRATANTE,), verificado=True
        ).perfil_contratante
    vaga = Vaga(
        contratante_id=contratante.id,
        categoria_id=categoria.id,
        endereco_id=criar_endereco(db, latitude=latitude, longitude=longitude).id,
        titulo="Serviço de teste",
        descricao="Descrição do serviço de teste",
        inicio_em=inicio_em or agora() + timedelta(days=1),
        duracao_minutos=duracao_minutos,
        valor_inicial=valor_inicial,
        valor_fixado=valor_fixado,
        estado=estado,
        profissional_id=profissional.id if profissional is not None else None,
    )
    db.add(vaga)
    db.commit()
    db.refresh(vaga)
    return vaga


def criar_cenario_vaga(
    db: Session,
    *,
    valor: Decimal = Decimal("150.00"),
    valor_fixado: Decimal | None = None,
    estado: EstadoVaga = EstadoVaga.ACEITA,
    inicio_em: datetime | None = None,
    duracao_minutos: int = 120,
    contratante: Usuario | None = None,
    profissional: Usuario | None = None,
) -> CenarioVaga:
    n = _proximo()
    contratante = contratante or criar_usuario(
        db, perfis=(TipoPerfil.CONTRATANTE,), perfil_ativo=TipoPerfil.CONTRATANTE
    )
    profissional = profissional or criar_usuario(
        db, perfis=(TipoPerfil.PROFISSIONAL,), perfil_ativo=TipoPerfil.PROFISSIONAL
    )
    categoria = Categoria(slug=f"categoria-{n}", nome=f"Categoria {n}", risco=RiscoCategoria.BAIXO)
    endereco = Endereco(bairro="Boa Viagem", latitude=-8.12, longitude=-34.90)
    db.add_all([categoria, endereco])
    db.flush()
    vaga = Vaga(
        contratante_id=contratante.perfil_contratante.id,
        categoria_id=categoria.id,
        profissional_id=profissional.perfil_profissional.id,
        endereco_id=endereco.id,
        titulo=f"Serviço {n}",
        descricao="Serviço de teste",
        inicio_em=inicio_em or agora() + timedelta(hours=1),
        duracao_minutos=duracao_minutos,
        valor_inicial=valor,
        valor_fixado=valor_fixado,
        estado=estado,
    )
    db.add(vaga)
    db.commit()
    db.refresh(vaga)
    return CenarioVaga(vaga=vaga, contratante=contratante, profissional=profissional)
