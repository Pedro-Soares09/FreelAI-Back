"""Serviço de chat (regra FLX07): ciclo de vida da conversa, histórico, visto e WebSocket.

Uma conversa nasce somente quando o convite da vaga é aceito (1 conversa por vaga, com vínculo
direto `vaga_id` <-> `conversa_id`). Permanece aberta para escrita enquanto a vaga está em
andamento e fecha para novas mensagens assim que a vaga cai (expirada, cancelada, falta,
reembolsada ou devolvida à publicação sem profissional) ou encerra seu ciclo financeiro
(liberada ou liberada parcial).
"""

import os
import re
import secrets
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from fastapi import WebSocket, status
from jose import JWTError
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.schemas.conversas import MensagemDetalhe, ResultadoVisto
from app.api.v1.schemas.integracao import ConversaResumo, MensagemResumo, PerfilResumo
from app.erros import erro_http
from app.models import (
    Avaliacao,
    Conversa,
    Denuncia,
    EstadoConta,
    EstadoDenuncia,
    EstadoVaga,
    Mensagem,
    OrigemDenuncia,
    PerfilContratante,
    PerfilProfissional,
    TentativaContato,
    Usuario,
    Vaga,
)
from app.security import decode_access_token
from app.services import comprovantes, configuracao
from app.tempo import agora, como_utc

WS_CODIGO_NAO_AUTENTICADO = 4401
WS_CODIGO_SEM_ACESSO = 4403
WS_CODIGO_NAO_ENCONTRADA = 4404

LIMITE_TENTATIVAS_CONTATO = 3
MOTIVO_DENUNCIA_CONTATO = "tentativa_de_contato_externo"
AVISO_CONTATO_BLOQUEADO = "Por segurança, não é permitido enviar telefone ou WhatsApp pelo chat."

BYTES_POR_MB = 1024 * 1024
TAMANHO_BLOCO_ANEXO = 64 * 1024

# Aceita formatos brasileiros com pontos, espaços, parênteses, hífen ou sem separadores
# (+55, DDD com ou sem 0, nono dígito e blocos 4+4), evitando confundir datas ou valores.
PADRAO_TELEFONE_BR = re.compile(
    r"(?<!\d)"
    r"(?:"
    r"(?:\+?55[.\s-]*|\(\+?55\)[.\s-]*)?"
    r"\(?0?[1-9]\d\)?[.\s-]*"
    r"(?:9[.\s-]?)?\d{4}[.\s-]*\d{4}"
    r"|"
    r"(?:9[.\s-]?)?\d{4}[.\s-]+\d{4}"
    r")"
    r"(?!\d)"
)
PADRAO_WHATSAPP = re.compile(
    r"(?:"
    r"wa\.me/"
    r"|api\.whatsapp\.com"
    r"|\b(?:whats\s*app|whats|wpp|zap\s*zap|zap)\b"
    r")",
    re.IGNORECASE,
)

PADRAO_NOME_ANEXO = re.compile(r"^[0-9a-f]{32}\.(jpg|png|pdf)$")
TIPOS_ANEXO_POR_EXTENSAO: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".pdf": "application/pdf",
}

# Estados da vaga a partir do aceite em que a conversa já pode existir.
ESTADOS_POS_ACEITE: frozenset[EstadoVaga] = frozenset(
    {
        EstadoVaga.ACEITA,
        EstadoVaga.AGUARDANDO_PAGAMENTO,
        EstadoVaga.CONTRATADA,
        EstadoVaga.EM_EXECUCAO,
        EstadoVaga.ENCERRADA,
        EstadoVaga.CONCLUIDA,
        EstadoVaga.EM_DISPUTA,
        EstadoVaga.LIBERADA,
        EstadoVaga.LIBERADA_PARCIAL,
        EstadoVaga.CANCELADA,
        EstadoVaga.EXPIRADA,
        EstadoVaga.FALTA,
        EstadoVaga.REEMBOLSADA,
    }
)

# Estados em que a conversa aceita envio de novas mensagens.
# Quando a vaga cai (expirada, cancelada, falta, reembolsada ou volta a publicada) ou já foi
# liquidada (liberada / liberada_parcial), a conversa fecha para escrita.
ESTADOS_CONVERSA_ABERTA: frozenset[EstadoVaga] = frozenset(
    {
        EstadoVaga.ACEITA,
        EstadoVaga.AGUARDANDO_PAGAMENTO,
        EstadoVaga.CONTRATADA,
        EstadoVaga.EM_EXECUCAO,
        EstadoVaga.ENCERRADA,
        EstadoVaga.CONCLUIDA,
        EstadoVaga.EM_DISPUTA,
    }
)


def _em_utc(instante: datetime | None) -> datetime | None:
    return como_utc(instante) if instante is not None else None


def vaga_permite_escrita(vaga: Vaga) -> bool:
    """Verdadeiro enquanto a vaga tem profissional vinculado e está em estado ativo."""
    return vaga.profissional_id is not None and vaga.estado in ESTADOS_CONVERSA_ABERTA


def conversa_bloqueada(db: Session, conversa: Conversa) -> bool:
    """Verdadeiro quando uma denúncia procedente bloqueou a troca de mensagens desta conversa.

    O bloqueio vale apenas para o chat entre as duas partes da vaga; o restante da plataforma
    segue disponível para elas.
    """
    return (
        db.scalar(
            select(Denuncia.id)
            .where(
                Denuncia.estado == EstadoDenuncia.PROCEDENTE,
                (Denuncia.conversa_id == conversa.id) | (Denuncia.vaga_id == conversa.vaga_id),
            )
            .limit(1)
        )
        is not None
    )


def exigir_conversa_desbloqueada(db: Session, conversa: Conversa) -> None:
    if conversa_bloqueada(db, conversa):
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "conversa_bloqueada",
            "O chat entre as partes foi bloqueado após uma denúncia procedente.",
        )


def sincronizar_estado_conversa(db: Session, conversa: Conversa, vaga: Vaga | None = None) -> bool:
    """Atualiza `conversa.aberta` conforme o estado atual da vaga e devolve o valor efetivo."""
    vaga_alvo = vaga or db.get(Vaga, conversa.vaga_id)
    deve_estar_aberta = vaga_alvo is not None and vaga_permite_escrita(vaga_alvo)
    if conversa.aberta != deve_estar_aberta:
        conversa.aberta = deve_estar_aberta
        db.commit()
        db.refresh(conversa)
    return conversa.aberta


def abrir_conversa_no_aceite(db: Session, vaga: Vaga) -> Conversa:
    """Cria (ou recupera/reabre de forma idempotente) a conversa da vaga no aceite do convite.

    Recusa abrir conversa antes do aceite (`publicada` ou `aguardando_aceite`) ou quando não há
    profissional vinculado à vaga.
    """
    if vaga.profissional_id is None or vaga.estado not in ESTADOS_POS_ACEITE:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "vaga_sem_aceite",
            "A conversa só é criada depois que o convite é aceito.",
            estado_atual=vaga.estado.value,
        )
    existente = db.scalar(select(Conversa).where(Conversa.vaga_id == vaga.id))
    aberta = vaga_permite_escrita(vaga)
    if existente is not None:
        if existente.aberta != aberta:
            existente.aberta = aberta
            db.commit()
            db.refresh(existente)
        return existente

    conversa = Conversa(vaga_id=vaga.id, aberta=aberta)
    db.add(conversa)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existente = db.scalar(select(Conversa).where(Conversa.vaga_id == vaga.id))
        if existente is not None:
            return existente
        raise
    db.refresh(conversa)
    return conversa


def fechar_conversa_da_vaga(db: Session, vaga: Vaga) -> Conversa | None:
    """Fecha a conversa para escrita quando a vaga cai ou é encerrada."""
    conversa = db.scalar(select(Conversa).where(Conversa.vaga_id == vaga.id))
    if conversa is not None and conversa.aberta:
        conversa.aberta = False
        db.commit()
        db.refresh(conversa)
    return conversa


def _usuarios_da_vaga(db: Session, vaga: Vaga) -> tuple[int | None, int | None]:
    """Devolve `(contratante_usuario_id, profissional_usuario_id)` da vaga."""
    contratante_uid = db.scalar(
        select(PerfilContratante.usuario_id).where(PerfilContratante.id == vaga.contratante_id)
    )
    profissional_uid = None
    if vaga.profissional_id is not None:
        profissional_uid = db.scalar(
            select(PerfilProfissional.usuario_id).where(
                PerfilProfissional.id == vaga.profissional_id
            )
        )
    return contratante_uid, profissional_uid


def eh_participante_da_vaga(db: Session, usuario_id: int, vaga: Vaga) -> bool:
    contratante_uid, profissional_uid = _usuarios_da_vaga(db, vaga)
    return usuario_id in (contratante_uid, profissional_uid)


def buscar_conversa(db: Session, conversa_id: int) -> tuple[Conversa, Vaga]:
    conversa = db.get(Conversa, conversa_id)
    if conversa is None:
        raise erro_http(
            status.HTTP_404_NOT_FOUND,
            "conversa_nao_encontrada",
            "Conversa não encontrada.",
        )
    vaga = db.get(Vaga, conversa.vaga_id)
    if vaga is None:
        raise erro_http(
            status.HTTP_404_NOT_FOUND,
            "vaga_nao_encontrada",
            "Vaga da conversa não encontrada.",
        )
    sincronizar_estado_conversa(db, conversa, vaga)
    return conversa, vaga


def exigir_participante(db: Session, conversa_id: int, usuario: Usuario) -> tuple[Conversa, Vaga]:
    """Garante que a conversa existe e que `usuario` faz parte da vaga correspondente."""
    conversa, vaga = buscar_conversa(db, conversa_id)
    if not eh_participante_da_vaga(db, usuario.id, vaga):
        raise erro_http(
            status.HTTP_403_FORBIDDEN,
            "sem_acesso_a_conversa",
            "Somente o contratante da vaga e o profissional contratado acessam esta conversa.",
        )
    return conversa, vaga


def buscar_conversa_da_vaga(db: Session, vaga_id: int, usuario: Usuario) -> tuple[Conversa, Vaga]:
    """Retorna a conversa ligada a `vaga_id` para um dos participantes."""
    vaga = db.get(Vaga, vaga_id)
    if vaga is None:
        raise erro_http(
            status.HTTP_404_NOT_FOUND,
            "vaga_nao_encontrada",
            "Vaga não encontrada.",
        )
    contratante_uid, profissional_uid = _usuarios_da_vaga(db, vaga)
    if usuario.id not in (contratante_uid, profissional_uid):
        raise erro_http(
            status.HTTP_403_FORBIDDEN,
            "sem_acesso_a_conversa",
            "Somente o contratante da vaga e o profissional contratado acessam esta conversa.",
        )
    conversa = db.scalar(select(Conversa).where(Conversa.vaga_id == vaga.id))
    if conversa is None:
        raise erro_http(
            status.HTTP_404_NOT_FOUND,
            "conversa_nao_encontrada",
            "Esta vaga ainda não possui conversa aberta.",
        )
    sincronizar_estado_conversa(db, conversa, vaga)
    return conversa, vaga


def _montar_perfil_resumo(
    db: Session,
    usuario_id: int,
    *,
    lado: str,
) -> PerfilResumo:
    usuario = db.get(Usuario, usuario_id)
    minimo_novo = configuracao.get("servicos_para_deixar_de_ser_novo", db=db)

    foto_url: str | None = None
    verificado = False
    novo = False
    cor_destaque: str | None = None

    if lado == "profissional" and usuario is not None and usuario.perfil_profissional is not None:
        perfil_prof = usuario.perfil_profissional
        foto_url = perfil_prof.foto_url
        verificado = perfil_prof.verificado
        novo = perfil_prof.servicos_concluidos < minimo_novo
    elif lado == "contratante" and usuario is not None and usuario.perfil_contratante is not None:
        perfil_cont = usuario.perfil_contratante
        verificado = perfil_cont.verificado
        cor_destaque = perfil_cont.cor_destaque
        foto_url = perfil_cont.avatar_url
        if foto_url is None and usuario.perfil_profissional is not None:
            foto_url = usuario.perfil_profissional.foto_url

    instante = agora()
    media, total = db.execute(
        select(func.avg(Avaliacao.nota), func.count(Avaliacao.id)).where(
            Avaliacao.avaliado_id == usuario_id,
            Avaliacao.visivel_em.is_not(None),
            Avaliacao.visivel_em <= instante,
        )
    ).one()

    destaque = (
        comprovantes.destaque_do_contratante(db, usuario_id) if lado == "contratante" else None
    )
    return PerfilResumo(
        usuario_id=usuario_id,
        nome=usuario.nome if usuario is not None else "",
        foto_url=foto_url,
        verificado=verificado,
        novo=novo,
        pro=False,
        nota_media=round(float(media), 2) if media is not None else None,
        total_avaliacoes=int(total or 0),
        adicionais_pagos=destaque.adicionais_pagos if destaque else 0,
        destaque=destaque.destaque if destaque else False,
        cor_destaque=cor_destaque,
    )


def _outra_parte_da_vaga(db: Session, vaga: Vaga, usuario_id: int) -> PerfilResumo:
    contratante_uid, profissional_uid = _usuarios_da_vaga(db, vaga)
    if usuario_id == contratante_uid and profissional_uid is not None:
        return _montar_perfil_resumo(db, profissional_uid, lado="profissional")
    if contratante_uid is not None:
        return _montar_perfil_resumo(db, contratante_uid, lado="contratante")
    return _montar_perfil_resumo(db, usuario_id, lado="contratante")


def montar_resumo(db: Session, conversa: Conversa, vaga: Vaga, usuario_id: int) -> ConversaResumo:
    """Monta `ConversaResumo` com `vaga_id`, `outra_parte`, `ultima_mensagem` e `nao_lidas`."""
    sincronizar_estado_conversa(db, conversa, vaga)
    ultima = db.scalar(
        select(Mensagem)
        .where(Mensagem.conversa_id == conversa.id)
        .order_by(Mensagem.id.desc())
        .limit(1)
    )
    nao_lidas = db.scalar(
        select(func.count(Mensagem.id)).where(
            Mensagem.conversa_id == conversa.id,
            Mensagem.autor_id != usuario_id,
            Mensagem.lida_em.is_(None),
        )
    )
    resumo_ultima = (
        MensagemResumo(
            autor_id=ultima.autor_id,
            texto=ultima.texto,
            enviada_em=_em_utc(ultima.enviada_em),
        )
        if ultima is not None
        else None
    )
    return ConversaResumo(
        id=conversa.id,
        vaga_id=vaga.id,
        aberta=conversa.aberta,
        bloqueada=conversa_bloqueada(db, conversa),
        outra_parte=_outra_parte_da_vaga(db, vaga, usuario_id),
        ultima_mensagem=resumo_ultima,
        nao_lidas=int(nao_lidas or 0),
    )


def listar_conversas_do_usuario(db: Session, usuario: Usuario) -> list[ConversaResumo]:
    """Lista todas as conversas em que o usuário é contratante ou profissional contratado."""
    filtros = []
    if usuario.perfil_contratante is not None:
        filtros.append(Vaga.contratante_id == usuario.perfil_contratante.id)
    if usuario.perfil_profissional is not None:
        filtros.append(Vaga.profissional_id == usuario.perfil_profissional.id)
    if not filtros:
        return []

    pares = db.execute(
        select(Conversa, Vaga)
        .join(Vaga, Vaga.id == Conversa.vaga_id)
        .where(or_(*filtros))
        .order_by(Conversa.id.desc())
    ).all()

    resumos = [montar_resumo(db, conversa, vaga, usuario.id) for conversa, vaga in pares]
    resumos.sort(
        key=lambda r: (
            r.ultima_mensagem.enviada_em
            if r.ultima_mensagem is not None
            else datetime.min.replace(tzinfo=como_utc(agora()).tzinfo),
            r.id,
        ),
        reverse=True,
    )
    return resumos


def montar_mensagem(mensagem: Mensagem) -> MensagemDetalhe:
    return MensagemDetalhe(
        id=mensagem.id,
        conversa_id=mensagem.conversa_id,
        autor_id=mensagem.autor_id,
        texto=mensagem.texto,
        anexo_url=mensagem.anexo_url,
        enviada_em=_em_utc(mensagem.enviada_em),
        lida_em=_em_utc(mensagem.lida_em),
    )


def listar_historico(db: Session, conversa_id: int, usuario: Usuario) -> list[MensagemDetalhe]:
    """Histórico completo da conversa em ordem cronológica."""
    conversa, _ = exigir_participante(db, conversa_id, usuario)
    mensagens = db.scalars(
        select(Mensagem).where(Mensagem.conversa_id == conversa.id).order_by(Mensagem.id.asc())
    ).all()
    return [montar_mensagem(item) for item in mensagens]


def contem_contato_proibido(texto: str) -> bool:
    """Identifica número de telefone brasileiro ou referência ao WhatsApp."""
    return bool(PADRAO_TELEFONE_BR.search(texto) or PADRAO_WHATSAPP.search(texto))


def tentativas_de_contato(db: Session, conversa_id: int, usuario_id: int) -> int:
    """Total persistido de tentativas bloqueadas de contato do usuário nesta conversa."""
    total = db.scalar(
        select(func.count(TentativaContato.id)).where(
            TentativaContato.conversa_id == conversa_id,
            TentativaContato.usuario_id == usuario_id,
        )
    )
    return int(total or 0)


def _registrar_tentativa_contato(
    db: Session,
    conversa: Conversa,
    vaga: Vaga,
    autor: Usuario,
    texto: str,
) -> tuple[int, Denuncia | None]:
    """Persiste a tentativa bloqueada e abre denúncia automática a partir da 3ª ocorrência."""
    db.add(
        TentativaContato(
            conversa_id=conversa.id,
            usuario_id=autor.id,
            conteudo=texto,
        )
    )
    db.flush()
    tentativas = tentativas_de_contato(db, conversa.id, autor.id)

    denuncia: Denuncia | None = None
    if tentativas >= LIMITE_TENTATIVAS_CONTATO:
        denuncia = db.scalar(
            select(Denuncia)
            .where(
                Denuncia.conversa_id == conversa.id,
                Denuncia.denunciado_id == autor.id,
                Denuncia.origem == OrigemDenuncia.AUTOMATICA,
                Denuncia.motivo == MOTIVO_DENUNCIA_CONTATO,
                Denuncia.estado == EstadoDenuncia.ABERTA,
            )
            .order_by(Denuncia.id.desc())
        )
        if denuncia is None:
            denuncia = Denuncia(
                denunciante_id=None,
                denunciado_id=autor.id,
                vaga_id=vaga.id,
                conversa_id=conversa.id,
                origem=OrigemDenuncia.AUTOMATICA,
                motivo=MOTIVO_DENUNCIA_CONTATO,
                descricao=(
                    f"Usuário atingiu {tentativas} tentativas bloqueadas de envio de contato "
                    f"externo na conversa {conversa.id}. Último texto: {texto[:240]}"
                ),
                estado=EstadoDenuncia.ABERTA,
            )
            db.add(denuncia)

    db.commit()
    return tentativas, denuncia


def _preparar_texto_e_bloquear_contato(
    db: Session,
    conversa: Conversa,
    vaga: Vaga,
    autor: Usuario,
    texto: str | None,
) -> str | None:
    texto_limpo = texto.strip() if texto is not None else None
    texto_limpo = texto_limpo or None
    if texto_limpo is not None and contem_contato_proibido(texto_limpo):
        tentativas, denuncia = _registrar_tentativa_contato(db, conversa, vaga, autor, texto_limpo)
        raise erro_http(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "contato_externo_bloqueado",
            AVISO_CONTATO_BLOQUEADO,
            tentativas=tentativas,
            denuncia_aberta=denuncia is not None,
        )
    return texto_limpo


def registrar_mensagem(
    db: Session,
    conversa: Conversa,
    vaga: Vaga,
    autor: Usuario,
    *,
    texto: str | None = None,
    anexo_url: str | None = None,
) -> Mensagem:
    """Persiste uma nova mensagem na conversa se ela estiver aberta para escrita."""
    exigir_conversa_desbloqueada(db, conversa)
    aberta = sincronizar_estado_conversa(db, conversa, vaga)
    if not aberta:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "conversa_fechada",
            "Esta conversa está fechada para novas mensagens.",
            vaga_estado=vaga.estado.value,
        )
    anexo_limpo = anexo_url.strip() if anexo_url is not None else None
    anexo_limpo = anexo_limpo or None
    texto_limpo = _preparar_texto_e_bloquear_contato(db, conversa, vaga, autor, texto)
    if texto_limpo is None and anexo_limpo is None:
        raise erro_http(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "mensagem_vazia",
            "A mensagem precisa ter texto ou anexo.",
        )

    mensagem = Mensagem(
        conversa_id=conversa.id,
        autor_id=autor.id,
        texto=texto_limpo,
        anexo_url=anexo_limpo,
    )
    db.add(mensagem)
    db.commit()
    db.refresh(mensagem)
    return mensagem


@dataclass(frozen=True)
class FormatoAnexo:
    extensao: str
    content_type: str


def obter_pasta_anexos() -> Path:
    """Diretório local onde os anexos do chat são gravados."""
    destino = Path(os.getenv("FREELAI_PASTA_ANEXOS", "./uploads/conversas")).resolve()
    destino.mkdir(parents=True, exist_ok=True)
    return destino


def _ler_anexo_com_limite(fluxo: BinaryIO, db: Session) -> bytes:
    limite_mb = max(1, int(configuracao.get("anexo_max_mb", db) or 10))
    limite_bytes = limite_mb * BYTES_POR_MB
    partes: list[bytes] = []
    total = 0

    fluxo.seek(0)
    while True:
        bloco = fluxo.read(TAMANHO_BLOCO_ANEXO)
        if not bloco:
            break
        total += len(bloco)
        if total > limite_bytes:
            raise erro_http(
                status.HTTP_413_CONTENT_TOO_LARGE,
                "arquivo_muito_grande",
                f"O anexo excede o limite de {limite_mb} MB.",
                limite_mb=limite_mb,
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


def _detectar_formato_anexo(conteudo: bytes) -> FormatoAnexo:
    if conteudo.startswith(b"\xff\xd8\xff"):
        return FormatoAnexo("jpg", "image/jpeg")
    if conteudo.startswith(b"\x89PNG\r\n\x1a\n"):
        return FormatoAnexo("png", "image/png")
    if conteudo.startswith(b"%PDF-"):
        return FormatoAnexo("pdf", "application/pdf")
    raise erro_http(
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        "formato_nao_suportado",
        "Envie apenas imagens JPG/PNG ou arquivos PDF.",
    )


def registrar_mensagem_com_anexo(
    db: Session,
    conversa: Conversa,
    vaga: Vaga,
    autor: Usuario,
    *,
    fluxo: BinaryIO,
    texto: str | None = None,
) -> Mensagem:
    """Valida o anexo e a legenda opcional, grava o arquivo e persiste a mensagem."""
    exigir_conversa_desbloqueada(db, conversa)
    aberta = sincronizar_estado_conversa(db, conversa, vaga)
    if not aberta:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "conversa_fechada",
            "Esta conversa está fechada para novas mensagens.",
            vaga_estado=vaga.estado.value,
        )

    texto_limpo = _preparar_texto_e_bloquear_contato(db, conversa, vaga, autor, texto)
    conteudo = _ler_anexo_com_limite(fluxo, db)
    formato = _detectar_formato_anexo(conteudo)

    nome_arquivo = f"{secrets.token_hex(16)}.{formato.extensao}"
    pasta_conversa = obter_pasta_anexos() / str(conversa.id)
    pasta_conversa.mkdir(parents=True, exist_ok=True)
    caminho = pasta_conversa / nome_arquivo
    caminho.write_bytes(conteudo)

    anexo_url = f"/api/v1/conversas/{conversa.id}/anexos/{nome_arquivo}"
    mensagem = Mensagem(
        conversa_id=conversa.id,
        autor_id=autor.id,
        texto=texto_limpo,
        anexo_url=anexo_url,
    )
    try:
        db.add(mensagem)
        db.commit()
        db.refresh(mensagem)
    except Exception:
        caminho.unlink(missing_ok=True)
        raise
    return mensagem


def ler_anexo_chat(conversa_id: int, nome: str) -> tuple[bytes, str]:
    """Lê um anexo salvo localmente validando nome seguro e existência."""
    if not PADRAO_NOME_ANEXO.fullmatch(nome):
        raise erro_http(
            status.HTTP_404_NOT_FOUND,
            "anexo_nao_encontrado",
            "Anexo não encontrado.",
        )
    caminho = obter_pasta_anexos() / str(conversa_id) / nome
    if not caminho.is_file():
        raise erro_http(
            status.HTTP_404_NOT_FOUND,
            "anexo_nao_encontrado",
            "Anexo não encontrado.",
        )
    media_type = TIPOS_ANEXO_POR_EXTENSAO.get(caminho.suffix.lower(), "application/octet-stream")
    return caminho.read_bytes(), media_type


def marcar_visto(db: Session, conversa_id: int, usuario: Usuario) -> ResultadoVisto:
    """Marca como lidas todas as mensagens recebidas pelo usuário na conversa."""
    conversa, _ = exigir_participante(db, conversa_id, usuario)
    instante = agora()
    resultado = db.execute(
        update(Mensagem)
        .where(
            Mensagem.conversa_id == conversa.id,
            Mensagem.autor_id != usuario.id,
            Mensagem.lida_em.is_(None),
        )
        .values(lida_em=instante)
    )
    db.commit()
    marcadas = int(resultado.rowcount or 0)
    return ResultadoVisto(
        conversa_id=conversa.id,
        marcadas=marcadas,
        lida_em=_em_utc(instante) if marcadas > 0 else None,
    )


def conta_ativa_no_ws(db: Session, usuario: Usuario) -> bool:
    """Confirma que a conta não está banida nem suspensa (reativa suspensão já vencida)."""
    if usuario.estado == EstadoConta.BANIDA:
        return False
    if usuario.estado == EstadoConta.SUSPENSA:
        retorno = usuario.suspensa_ate and como_utc(usuario.suspensa_ate)
        if retorno is not None and retorno <= agora():
            usuario.estado = EstadoConta.ATIVA
            usuario.suspensa_ate = None
            db.commit()
        else:
            return False
    return True


def autenticar_token_ws(db: Session, token: str | None) -> Usuario | None:
    """Valida o JWT do WebSocket (query string, cabeçalho Bearer ou primeira mensagem).

    Devolve `None` quando o token está ausente, inválido, expirado ou pertence a uma conta
    banida ou suspensa.
    """
    if not token:
        return None
    try:
        carga = decode_access_token(token)
        usuario_id = int(carga["sub"])
    except (JWTError, KeyError, ValueError):
        return None

    usuario = db.get(Usuario, usuario_id)
    if usuario is None or not conta_ativa_no_ws(db, usuario):
        return None
    return usuario


class GerenciadorConexoes:
    """Gerenciador em memória (1 processo) das conexões WebSocket por conversa."""

    def __init__(self) -> None:
        self._salas: dict[int, list[WebSocket]] = defaultdict(list)

    def registrar(self, conversa_id: int, websocket: WebSocket) -> None:
        conexoes = self._salas[conversa_id]
        if websocket not in conexoes:
            conexoes.append(websocket)

    def desconectar(self, conversa_id: int, websocket: WebSocket) -> None:
        conexoes = self._salas.get(conversa_id)
        if not conexoes:
            return
        if websocket in conexoes:
            conexoes.remove(websocket)
        if not conexoes:
            self._salas.pop(conversa_id, None)

    def conexoes_ativas(self, conversa_id: int) -> int:
        return len(self._salas.get(conversa_id, ()))

    async def transmitir(self, conversa_id: int, carga: dict) -> None:
        """Envia `carga` em JSON para todos os participantes conectados na conversa."""
        conexoes = list(self._salas.get(conversa_id, ()))
        inativas: list[WebSocket] = []
        for conexao in conexoes:
            try:
                await conexao.send_json(carga)
            except Exception:
                inativas.append(conexao)
        for conexao in inativas:
            self.desconectar(conversa_id, conexao)


gerenciador_conexoes = GerenciadorConexoes()
