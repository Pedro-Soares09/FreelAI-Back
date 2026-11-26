"""Rotas dos comprovantes de pagamento da vaga (serviço, horas extras e bônus)."""

from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.v1.schemas.comprovantes import ComprovanteDetalhe, ResumoComprovantes
from app.db import get_db
from app.deps import exige_conta_ativa, usuario_atual
from app.erros import erro_http
from app.models import TipoComprovante, Usuario, Vaga
from app.services import comprovantes

router = APIRouter(tags=["comprovantes"])


def _vaga(db: Session, vaga_id: int) -> Vaga:
    vaga = db.get(Vaga, vaga_id)
    if vaga is None:
        raise erro_http(status.HTTP_404_NOT_FOUND, "vaga_nao_encontrada", "Vaga não encontrada.")
    return vaga


@router.post(
    "/vagas/{vaga_id}/comprovantes",
    response_model=ComprovanteDetalhe,
    status_code=status.HTTP_201_CREATED,
)
def anexar_comprovante(
    vaga_id: int,
    tipo: TipoComprovante = Form(description="servico, horas_extras ou bonus."),
    valor: Decimal = Form(gt=0, description="Valor pago, em reais."),
    arquivo: UploadFile = File(description="Comprovante em jpg, png ou pdf."),
    acordo_previo: str | None = Form(
        default=None, description="Registro do acordo prévio (obrigatório em horas extras)."
    ),
    usuario: Usuario = Depends(usuario_atual),
    _: Usuario = Depends(exige_conta_ativa),
    db: Session = Depends(get_db),
) -> ComprovanteDetalhe:
    """Anexa o comprovante de pagamento. Horas extras e bônus pagam a taxa adicional."""
    return comprovantes.anexar(
        db,
        _vaga(db, vaga_id),
        usuario,
        tipo=tipo,
        valor=valor,
        fluxo=arquivo.file,
        nome_original=arquivo.filename,
        content_type=arquivo.content_type,
        acordo_previo=acordo_previo,
    )


@router.get("/vagas/{vaga_id}/comprovantes", response_model=ResumoComprovantes)
def listar_comprovantes(
    vaga_id: int, usuario: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)
) -> ResumoComprovantes:
    """Comprovantes da vaga com totais e a taxa adicional da plataforma."""
    vaga = _vaga(db, vaga_id)
    comprovantes.exigir_parte_ou_admin(db, vaga, usuario)
    return comprovantes.resumir(db, vaga)


@router.get("/vagas/{vaga_id}/comprovantes/{comprovante_id}/arquivo")
def baixar_comprovante(
    vaga_id: int,
    comprovante_id: int,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> Response:
    """Serve o arquivo do comprovante às partes da vaga e à administração."""
    vaga = _vaga(db, vaga_id)
    comprovantes.exigir_parte_ou_admin(db, vaga, usuario)
    registro, conteudo = comprovantes.ler_arquivo(db, vaga, comprovante_id)
    return Response(
        content=conteudo,
        media_type=registro.mime_type,
        headers={"X-Content-Type-Options": "nosniff"},
    )
