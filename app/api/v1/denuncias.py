"""Rotas públicas/autenticadas de denúncia (chat e perfil) — US57 (SEG05)."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.v1.schemas.denuncias import DenunciaEntrada, DenunciaPublicaResumo
from app.db import get_db
from app.deps import usuario_atual
from app.models import Usuario
from app.services import denuncias

router = APIRouter(prefix="/denuncias", tags=["denuncias"])


@router.post("", response_model=DenunciaPublicaResumo, status_code=status.HTTP_201_CREATED)
def registrar_denuncia(
    entrada: DenunciaEntrada,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> DenunciaPublicaResumo:
    """Registra uma denúncia a partir de um perfil, vaga ou conversa. Entra na fila do admin."""
    denuncia = denuncias.criar_denuncia(db, usuario, entrada)
    return denuncias.montar_resumo_publico(denuncia)


@router.get("", response_model=list[DenunciaPublicaResumo])
def listar_minhas_denuncias(
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> list[DenunciaPublicaResumo]:
    """Lista denúncias ligadas ao usuário, sem jamais expor quem denunciou (`denunciante_id`)."""
    return denuncias.listar_denuncias_do_usuario(db, usuario)


@router.get("/{denuncia_id}", response_model=DenunciaPublicaResumo)
def detalhar_denuncia(
    denuncia_id: int,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> DenunciaPublicaResumo:
    """Consulta o estado de uma denúncia sem revelar o autor ao denunciado."""
    return denuncias.consultar_denuncia_publica(db, denuncia_id, usuario)
