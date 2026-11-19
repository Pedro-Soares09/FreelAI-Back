"""Rotas de envio e leitura de arquivos: foto, selfie, documento, qualificação, EPI e anexo."""

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.v1.schemas.arquivos import ArquivoEnviado
from app.db import get_db
from app.deps import usuario_atual, usuario_opcional
from app.models import TipoArquivo, Usuario
from app.services import arquivos
from app.tempo import como_utc

router = APIRouter(prefix="/arquivos", tags=["arquivos"])


@router.post("", response_model=ArquivoEnviado, status_code=status.HTTP_201_CREATED)
def enviar_arquivo(
    tipo: TipoArquivo = Form(
        description="Finalidade do arquivo: foto, selfie, documento, qualificacao, epi ou anexo."
    ),
    arquivo: UploadFile = File(description="Arquivo em formato jpg, png ou pdf (até 10 MB)."),
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> ArquivoEnviado:
    """Valida tipo (jpg, png, pdf) e tamanho (até 10 MB), armazena com nome gerado e retorna URL."""
    registro = arquivos.salvar_upload(
        db,
        usuario,
        tipo,
        arquivo.file,
        nome_original=arquivo.filename,
        content_type=arquivo.content_type,
    )
    return ArquivoEnviado(
        id=registro.id,
        tipo=registro.tipo,
        url=arquivos.url_do_arquivo(registro.chave),
        mime_type=registro.mime_type,
        tamanho_bytes=registro.tamanho_bytes,
        criado_em=como_utc(registro.criado_em),
    )


@router.get("/{tipo}/{nome}")
def baixar_arquivo(
    tipo: TipoArquivo,
    nome: str,
    usuario: Usuario | None = Depends(usuario_opcional),
    db: Session = Depends(get_db),
) -> Response:
    """Serve o arquivo armazenado. Documento e selfie exigem ser o dono ou administrador."""
    chave = f"{tipo.value}/{nome}"
    registro, conteudo = arquivos.obter_arquivo_para_leitura(db, chave, usuario)
    return Response(
        content=conteudo,
        media_type=registro.mime_type,
        headers={"X-Content-Type-Options": "nosniff"},
    )
