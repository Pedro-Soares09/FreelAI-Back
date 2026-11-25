"""Cadastro dos perfis, comprovações do profissional e requisitos de segurança por categoria."""

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.schemas.cadastro import (
    AtualizacaoProfissionalEntrada,
    CadastroContratanteEntrada,
    CadastroProfissionalEntrada,
    ComprovacaoDetalhe,
    EpiEntrada,
    ExperienciaEntrada,
    ItemChecklist,
    PerfilContratanteDetalhe,
    PerfilProfissionalDetalhe,
    QualificacaoEntrada,
    SegurancaDaCategoria,
)
from app.api.v1.schemas.perfil_contratante import (
    AtualizacaoContratanteEntrada,
    PerfilPublicoContratante,
)
from app.db import get_db
from app.deps import usuario_atual
from app.erros import erro_http
from app.models import Categoria, Usuario
from app.services import cadastro, perfil_contratante

router = APIRouter(tags=["perfis"])


def _profissional_ou_404(usuario: Usuario):
    if usuario.perfil_profissional is None:
        raise erro_http(
            status.HTTP_404_NOT_FOUND,
            "perfil_nao_encontrado",
            "Perfil profissional não cadastrado.",
        )
    return usuario.perfil_profissional


@router.post(
    "/perfis/profissional",
    response_model=PerfilProfissionalDetalhe,
    status_code=status.HTTP_201_CREATED,
)
def cadastrar_profissional(
    entrada: CadastroProfissionalEntrada,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> PerfilProfissionalDetalhe:
    """Cadastra o perfil profissional com localização, categorias e experiências."""
    perfil = cadastro.criar_perfil_profissional(db, usuario, entrada)
    return cadastro.detalhar_profissional(db, perfil)


@router.get("/perfis/profissional/meu", response_model=PerfilProfissionalDetalhe)
def consultar_meu_perfil_profissional(
    usuario: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)
) -> PerfilProfissionalDetalhe:
    return cadastro.detalhar_profissional(db, _profissional_ou_404(usuario))


@router.patch("/perfis/profissional/meu", response_model=PerfilProfissionalDetalhe)
def atualizar_meu_perfil_profissional(
    entrada: AtualizacaoProfissionalEntrada,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> PerfilProfissionalDetalhe:
    perfil = cadastro.atualizar_perfil_profissional(db, _profissional_ou_404(usuario), entrada)
    return cadastro.detalhar_profissional(db, perfil)


@router.post(
    "/perfis/contratante",
    response_model=PerfilContratanteDetalhe,
    status_code=status.HTTP_201_CREATED,
)
def cadastrar_contratante(
    entrada: CadastroContratanteEntrada,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> PerfilContratanteDetalhe:
    """Cadastra o perfil contratante, pessoa física ou jurídica (CNPJ opcional)."""
    return cadastro.detalhar_contratante(
        db, cadastro.criar_perfil_contratante(db, usuario, entrada)
    )


@router.get("/perfis/contratante/meu", response_model=PerfilContratanteDetalhe)
def consultar_meu_perfil_contratante(
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> PerfilContratanteDetalhe:
    if usuario.perfil_contratante is None:
        raise erro_http(
            status.HTTP_404_NOT_FOUND, "perfil_nao_encontrado", "Perfil contratante não cadastrado."
        )
    return cadastro.detalhar_contratante(db, usuario.perfil_contratante)


@router.patch("/perfis/contratante/meu", response_model=PerfilContratanteDetalhe)
def atualizar_meu_perfil_contratante(
    entrada: AtualizacaoContratanteEntrada,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> PerfilContratanteDetalhe:
    """Edita bio, bairro, horário de contato, preferências, cor de destaque e avatar."""
    if usuario.perfil_contratante is None:
        raise erro_http(
            status.HTTP_404_NOT_FOUND, "perfil_nao_encontrado", "Perfil contratante não cadastrado."
        )
    perfil = perfil_contratante.atualizar(db, usuario.perfil_contratante, entrada)
    return cadastro.detalhar_contratante(db, perfil)


@router.get("/contratantes/{usuario_id}/perfil", response_model=PerfilPublicoContratante)
def consultar_perfil_publico_do_contratante(
    usuario_id: int,
    _usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> PerfilPublicoContratante:
    """Visão pública do contratante, sem dados sensíveis, para quem está logado."""
    return perfil_contratante.perfil_publico(db, usuario_id)


# ---------------------------------------------------------------- comprovações


def _comprovacao(registro, tipo: str, slug: str | None) -> ComprovacaoDetalhe:
    return ComprovacaoDetalhe(
        id=registro.id,
        tipo=tipo,
        categoria=slug,
        descricao=getattr(registro, "descricao", None) or getattr(registro, "referencia", None),
        arquivo_url=registro.arquivo_url,
        status=registro.status,
        motivo_recusa=getattr(registro, "motivo_recusa", None),
    )


@router.get("/profissional/comprovacoes", response_model=list[ComprovacaoDetalhe])
def listar_comprovacoes(
    usuario: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)
) -> list[ComprovacaoDetalhe]:
    """Experiências, qualificações e EPIs enviados, com o status de cada um."""
    _profissional_ou_404(usuario)
    return cadastro.listar_comprovacoes(db, usuario)


@router.post(
    "/profissional/experiencias",
    response_model=ComprovacaoDetalhe,
    status_code=status.HTTP_201_CREATED,
)
def enviar_experiencia(
    entrada: ExperienciaEntrada,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> ComprovacaoDetalhe:
    registro = cadastro.registrar_experiencia(db, usuario, entrada)
    return _comprovacao(registro, "experiencia", entrada.categoria)


@router.post(
    "/profissional/qualificacoes",
    response_model=ComprovacaoDetalhe,
    status_code=status.HTTP_201_CREATED,
)
def enviar_qualificacao(
    entrada: QualificacaoEntrada,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> ComprovacaoDetalhe:
    registro = cadastro.registrar_qualificacao(db, usuario, entrada)
    return _comprovacao(registro, "qualificacao", entrada.categoria)


@router.post(
    "/profissional/epis", response_model=ComprovacaoDetalhe, status_code=status.HTTP_201_CREATED
)
def enviar_epi(
    entrada: EpiEntrada,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> ComprovacaoDetalhe:
    registro = cadastro.registrar_epi(db, usuario, entrada)
    return _comprovacao(registro, "epi", entrada.categoria)


# ---------------------------------------------------------------- segurança por categoria


@router.get("/categorias/{slug}/seguranca", response_model=SegurancaDaCategoria)
def consultar_seguranca_da_categoria(
    slug: str,
    _: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> SegurancaDaCategoria:
    """EPIs e checklist de segurança da categoria, exibidos no cadastro e ao criar vaga."""
    categoria = db.scalar(select(Categoria).where(Categoria.slug == slug))
    if categoria is None:
        raise erro_http(
            status.HTTP_404_NOT_FOUND, "categoria_inexistente", "Categoria não encontrada."
        )
    return SegurancaDaCategoria(
        slug=categoria.slug,
        nome=categoria.nome,
        nivel_risco=categoria.risco,
        exige_qualificacao=categoria.exige_qualificacao,
        exige_epi=categoria.exige_epi,
        epis=[e.descricao for e in categoria.epis],
        checklist=[
            ItemChecklist(id=i.id, texto=i.texto, critico=i.critico) for i in categoria.checklist
        ],
    )
