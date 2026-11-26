"""Validação, armazenamento e controle de acesso dos arquivos enviados.

Módulo único para fotos de perfil, selfies, documentos de identidade, comprovantes de
qualificação, fotos de EPI e anexos de chat. Aceita apenas JPEG, PNG e PDF até o limite
configurado (`anexo_max_mb`), grava com nome gerado (nunca o original) e isola o destino por
trás do protocolo `ArmazenamentoArquivos`, pronto para ser trocado por S3.
"""

import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from fastapi import status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.erros import erro_http
from app.models import Arquivo, PapelUsuario, TipoArquivo, Usuario
from app.services import configuracao

BYTES_POR_MB = 1024 * 1024
TAMANHO_BLOCO = 64 * 1024

TIPOS_SENSIVEIS: frozenset[TipoArquivo] = frozenset(
    {TipoArquivo.DOCUMENTO, TipoArquivo.SELFIE, TipoArquivo.COMPROVANTE}
)


@dataclass(frozen=True)
class FormatoPermitido:
    extensao: str
    mime_type: str
    assinatura: bytes


FORMATOS: tuple[FormatoPermitido, ...] = (
    FormatoPermitido(extensao="jpg", mime_type="image/jpeg", assinatura=b"\xff\xd8\xff"),
    FormatoPermitido(
        extensao="png", mime_type="image/png", assinatura=b"\x89PNG\r\n\x1a\n"
    ),
    FormatoPermitido(extensao="pdf", mime_type="application/pdf", assinatura=b"%PDF-"),
)

EXTENSOES_PERMITIDAS = tuple(f.extensao for f in FORMATOS)
MIMES_PERMITIDOS: dict[str, FormatoPermitido] = {
    "image/jpeg": FORMATOS[0],
    "image/jpg": FORMATOS[0],
    "image/png": FORMATOS[1],
    "application/pdf": FORMATOS[2],
}
EXTENSOES_MAPEADAS: dict[str, FormatoPermitido] = {
    ".jpg": FORMATOS[0],
    ".jpeg": FORMATOS[0],
    ".png": FORMATOS[1],
    ".pdf": FORMATOS[2],
}


class ArmazenamentoArquivos(Protocol):
    """Contrato de armazenamento de binários (disco local hoje; S3 no futuro)."""

    def salvar(self, chave: str, conteudo: bytes, mime_type: str) -> None:
        """Persiste `conteudo` sob `chave`."""
        ...

    def ler(self, chave: str) -> bytes | None:
        """Devolve os bytes gravados em `chave` ou `None` quando não existir."""
        ...

    def remover(self, chave: str) -> None:
        """Remove `chave` se existir."""
        ...


class ArmazenamentoLocal:
    """Guarda os arquivos em subpastas dentro de `uploads_dir`, impedindo travessia de caminho."""

    def __init__(self, raiz: Path | str | None = None) -> None:
        self.raiz = Path(raiz if raiz is not None else settings.uploads_dir).resolve()

    def _caminho_seguro(self, chave: str) -> Path:
        Partes = Path(chave).parts
        if not Partes or ".." in Partes or Path(chave).is_absolute():
            raise ValueError("Chave de armazenamento inválida.")
        destino = (self.raiz / chave).resolve()
        if self.raiz not in destino.parents:
            raise ValueError("Chave aponta para fora da pasta de uploads.")
        return destino

    def salvar(self, chave: str, conteudo: bytes, mime_type: str) -> None:
        destino = self._caminho_seguro(chave)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(conteudo)

    def ler(self, chave: str) -> bytes | None:
        try:
            destino = self._caminho_seguro(chave)
        except ValueError:
            return None
        if not destino.is_file():
            return None
        return destino.read_bytes()

    def remover(self, chave: str) -> None:
        try:
            destino = self._caminho_seguro(chave)
        except ValueError:
            return
        destino.unlink(missing_ok=True)


def obter_armazenamento() -> ArmazenamentoArquivos:
    return ArmazenamentoLocal()


def url_do_arquivo(chave: str) -> str:
    return f"/api/v1/arquivos/{chave}"


def limite_bytes(db: Session | None = None) -> int:
    max_mb = int(configuracao.get("anexo_max_mb", db=db))
    return max_mb * BYTES_POR_MB


def _erro_tipo_invalido():
    return erro_http(
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        "arquivo_tipo_invalido",
        "Formato de arquivo não permitido. Envie apenas jpg, png ou pdf.",
        tipos_permitidos=list(EXTENSOES_PERMITIDAS),
    )


def detectar_formato(
    conteudo: bytes,
    *,
    nome_original: str | None = None,
    content_type: str | None = None,
) -> FormatoPermitido:
    """Identifica e valida o formato pelos bytes do cabeçalho, extensão e Content-Type."""
    formato_assinatura = next(
        (f for f in FORMATOS if conteudo.startswith(f.assinatura)),
        None,
    )
    if formato_assinatura is None:
        raise _erro_tipo_invalido()

    if nome_original:
        sufixo = Path(nome_original).suffix.lower()
        if sufixo:
            formato_ext = EXTENSOES_MAPEADAS.get(sufixo)
            if formato_ext is None or formato_ext != formato_assinatura:
                raise _erro_tipo_invalido()

    if content_type:
        mime_base = content_type.split(";", 1)[0].strip().lower()
        if mime_base and mime_base != "application/octet-stream":
            formato_mime = MIMES_PERMITIDOS.get(mime_base)
            if formato_mime is None or formato_mime != formato_assinatura:
                raise _erro_tipo_invalido()

    return formato_assinatura


def ler_com_limite(fluxo: BinaryIO, db: Session | None = None) -> bytes:
    """Lê o fluxo em blocos e interrompe assim que ultrapassar o limite configurado."""
    max_mb = int(configuracao.get("anexo_max_mb", db=db))
    max_bytes = max_mb * BYTES_POR_MB
    partes: list[bytes] = []
    total = 0

    while True:
        bloco = fluxo.read(TAMANHO_BLOCO)
        if not bloco:
            break
        total += len(bloco)
        if total > max_bytes:
            raise erro_http(
                status.HTTP_413_CONTENT_TOO_LARGE,
                "arquivo_muito_grande",
                f"O arquivo ultrapassa o limite de {max_mb} MB.",
                limite_mb=max_mb,
                limite_bytes=max_bytes,
            )
        partes.append(bloco)

    conteudo = b"".join(partes)
    if not conteudo:
        raise erro_http(
            status.HTTP_400_BAD_REQUEST,
            "arquivo_vazio",
            "O arquivo enviado está vazio.",
        )
    return conteudo


def gerar_chave(tipo: TipoArquivo, extensao: str) -> str:
    """Gera um caminho relativo opaco (`<tipo>/<token>.<extensao>`), sem usar o nome original."""
    return f"{tipo.value}/{secrets.token_hex(16)}.{extensao}"


def salvar_upload(
    db: Session,
    usuario: Usuario,
    tipo: TipoArquivo,
    fluxo: BinaryIO,
    *,
    nome_original: str | None = None,
    content_type: str | None = None,
    armazenamento: ArmazenamentoArquivos | None = None,
) -> Arquivo:
    """Valida tamanho e tipo, grava no armazenamento e registra o metadado no banco."""
    conteudo = ler_com_limite(fluxo, db=db)
    formato = detectar_formato(
        conteudo, nome_original=nome_original, content_type=content_type
    )
    chave = gerar_chave(tipo, formato.extensao)
    destino = armazenamento or obter_armazenamento()
    destino.salvar(chave, conteudo, formato.mime_type)

    registro = Arquivo(
        usuario_id=usuario.id,
        tipo=tipo,
        chave=chave,
        mime_type=formato.mime_type,
        tamanho_bytes=len(conteudo),
    )
    db.add(registro)
    try:
        db.commit()
    except Exception:
        db.rollback()
        destino.remover(chave)
        raise
    db.refresh(registro)
    return registro


def verificar_acesso(arquivo: Arquivo, usuario: Usuario | None) -> None:
    """Garante que documento e selfie só sejam lidos pelo dono ou pela administração."""
    if arquivo.tipo not in TIPOS_SENSIVEIS:
        return
    if usuario is None:
        erro = erro_http(
            status.HTTP_401_UNAUTHORIZED,
            "nao_autenticado",
            "Credenciais inválidas ou token expirado.",
        )
        erro.headers = {"WWW-Authenticate": "Bearer"}
        raise erro
    if usuario.id != arquivo.usuario_id and usuario.papel != PapelUsuario.ADMIN:
        raise erro_http(
            status.HTTP_403_FORBIDDEN,
            "sem_acesso_ao_arquivo",
            "Somente o dono do arquivo e a administração podem acessar este documento.",
        )


def obter_arquivo_para_leitura(
    db: Session,
    chave: str,
    usuario: Usuario | None,
    *,
    armazenamento: ArmazenamentoArquivos | None = None,
) -> tuple[Arquivo, bytes]:
    """Busca o registro, confere o controle de acesso por tipo e lê os bytes armazenados."""
    registro = db.scalar(select(Arquivo).where(Arquivo.chave == chave))
    if registro is None:
        raise erro_http(
            status.HTTP_404_NOT_FOUND,
            "arquivo_nao_encontrado",
            "Arquivo não encontrado.",
        )
    verificar_acesso(registro, usuario)
    origem = armazenamento or obter_armazenamento()
    conteudo = origem.ler(registro.chave)
    if conteudo is None:
        raise erro_http(
            status.HTTP_404_NOT_FOUND,
            "arquivo_nao_encontrado",
            "Arquivo não encontrado.",
        )
    return registro, conteudo
