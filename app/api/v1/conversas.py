"""Rotas REST e WebSocket de conversas e mensagens (regra FLX07).

A conversa nasce no aceite do convite (1 por vaga). Como o navegador não envia cabeçalhos no
handshake do WebSocket, o token JWT pode ir na query string (`?token=...`, validado antes do
`accept()`), na primeira mensagem JSON (`?auth=mensagem`) para evitar exposição em logs de acesso,
ou via cabeçalho `Authorization: Bearer <jwt>`. Falhas fecham a conexão com código 4401.
"""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.v1.schemas.conversas import (
    MensagemDetalhe,
    MensagemEntrada,
    ResultadoVisto,
)
from app.api.v1.schemas.integracao import ConversaResumo
from app.db import get_db
from app.deps import usuario_atual
from app.models import Usuario
from app.services import conversas
from app.services.conversas import (
    WS_CODIGO_NAO_AUTENTICADO,
    WS_CODIGO_NAO_ENCONTRADA,
    WS_CODIGO_SEM_ACESSO,
    gerenciador_conexoes,
)

WS_TIMEOUT_AUTH_SEGUNDOS = 5.0

router = APIRouter(tags=["conversas"])


@router.get("/conversas", response_model=list[ConversaResumo])
def listar_conversas(
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> list[ConversaResumo]:
    """Lista as conversas do usuário com a outra parte, última mensagem e total de não lidas."""
    return conversas.listar_conversas_do_usuario(db, usuario)


@router.get("/conversas/{conversa_id}", response_model=ConversaResumo)
def detalhar_conversa(
    conversa_id: int,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> ConversaResumo:
    """Resumo de uma conversa, com o id da vaga a que pertence."""
    conversa, vaga = conversas.exigir_participante(db, conversa_id, usuario)
    return conversas.montar_resumo(db, conversa, vaga, usuario.id)


@router.get("/vagas/{vaga_id}/conversa", response_model=ConversaResumo)
def consultar_conversa_da_vaga(
    vaga_id: int,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> ConversaResumo:
    """Ponto de integração entre `vaga_id` e `conversa_id`: devolve a conversa aberta no aceite."""
    conversa, vaga = conversas.buscar_conversa_da_vaga(db, vaga_id, usuario)
    return conversas.montar_resumo(db, conversa, vaga, usuario.id)


@router.get("/conversas/{conversa_id}/mensagens", response_model=list[MensagemDetalhe])
def listar_mensagens(
    conversa_id: int,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> list[MensagemDetalhe]:
    """Histórico completo de mensagens da conversa em ordem cronológica."""
    return conversas.listar_historico(db, conversa_id, usuario)


@router.post(
    "/conversas/{conversa_id}/mensagens",
    response_model=MensagemDetalhe,
    status_code=status.HTTP_201_CREATED,
)
async def enviar_mensagem(
    conversa_id: int,
    entrada: MensagemEntrada,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> MensagemDetalhe:
    """Persiste uma mensagem na conversa e transmite em tempo real aos conectados no WebSocket."""
    conversa, vaga = conversas.exigir_participante(db, conversa_id, usuario)
    mensagem = conversas.registrar_mensagem(
        db,
        conversa,
        vaga,
        usuario,
        texto=entrada.texto,
        anexo_url=entrada.anexo_url,
    )
    detalhe = conversas.montar_mensagem(mensagem)
    await gerenciador_conexoes.transmitir(
        conversa.id,
        detalhe.model_dump(mode="json"),
    )
    return detalhe


@router.post(
    "/conversas/{conversa_id}/anexos",
    response_model=MensagemDetalhe,
    status_code=status.HTTP_201_CREATED,
)
async def enviar_anexo(
    conversa_id: int,
    arquivo: UploadFile = File(..., description="Imagem JPG/PNG ou PDF até 10 MB."),
    texto: str | None = Form(default=None, max_length=4000, description="Legenda opcional."),
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> MensagemDetalhe:
    """Recebe um anexo (JPG, PNG ou PDF), cria a mensagem no chat e transmite em tempo real."""
    conversa, vaga = conversas.exigir_participante(db, conversa_id, usuario)
    mensagem = conversas.registrar_mensagem_com_anexo(
        db,
        conversa,
        vaga,
        usuario,
        fluxo=arquivo.file,
        texto=texto,
    )
    detalhe = conversas.montar_mensagem(mensagem)
    await gerenciador_conexoes.transmitir(
        conversa.id,
        detalhe.model_dump(mode="json"),
    )
    return detalhe


@router.get("/conversas/{conversa_id}/anexos/{nome}")
def baixar_anexo(
    conversa_id: int,
    nome: str,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> Response:
    """Entrega o conteúdo de um anexo do chat somente para os participantes da conversa."""
    conversas.exigir_participante(db, conversa_id, usuario)
    conteudo, media_type = conversas.ler_anexo_chat(conversa_id, nome)
    return Response(
        content=conteudo,
        media_type=media_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'inline; filename="{nome}"',
        },
    )


@router.post("/conversas/{conversa_id}/visto", response_model=ResultadoVisto)
def marcar_conversa_como_vista(
    conversa_id: int,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> ResultadoVisto:
    """Marca como vistas as mensagens enviadas pela outra parte na conversa."""
    return conversas.marcar_visto(db, conversa_id, usuario)


@contextmanager
def _sessao_ws(websocket: WebSocket) -> Iterator[Session]:
    """Abre e fecha uma sessão curta de banco por operação, respeitando overrides de teste."""
    provedor = websocket.app.dependency_overrides.get(get_db, get_db)
    with contextmanager(provedor)() as sessao:
        yield sessao


def _extrair_bearer(websocket: WebSocket) -> str | None:
    cabecalho = websocket.headers.get("authorization")
    if not cabecalho:
        return None
    esquema, _, token = cabecalho.partition(" ")
    if esquema.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _autenticar_e_autorizar_ws(
    websocket: WebSocket, conversa_id: int, token: str | None
) -> tuple[int, int] | int:
    """Valida token e acesso à conversa em uma sessão curta de banco."""
    with _sessao_ws(websocket) as db:
        usuario = conversas.autenticar_token_ws(db, token)
        if usuario is None:
            return WS_CODIGO_NAO_AUTENTICADO
        try:
            conversa, _ = conversas.exigir_participante(db, conversa_id, usuario)
        except HTTPException as erro:
            return (
                WS_CODIGO_NAO_ENCONTRADA
                if erro.status_code == status.HTTP_404_NOT_FOUND
                else WS_CODIGO_SEM_ACESSO
            )
        return usuario.id, conversa.id


async def _autenticar_primeira_mensagem(
    websocket: WebSocket, conversa_id: int
) -> tuple[int, int] | None:
    """Recebe o token JWT na primeira mensagem JSON após o handshake."""
    try:
        primeiro = await asyncio.wait_for(
            websocket.receive_json(), timeout=WS_TIMEOUT_AUTH_SEGUNDOS
        )
    except WebSocketDisconnect:
        return None
    except (TimeoutError, ValueError):
        await websocket.close(code=WS_CODIGO_NAO_AUTENTICADO)
        return None

    if (
        not isinstance(primeiro, dict)
        or primeiro.get("tipo", "auth") != "auth"
        or not isinstance(primeiro.get("token"), str)
        or not primeiro["token"].strip()
    ):
        await websocket.close(code=WS_CODIGO_NAO_AUTENTICADO)
        return None

    resultado = _autenticar_e_autorizar_ws(
        websocket, conversa_id, primeiro["token"].strip()
    )
    if isinstance(resultado, int):
        await websocket.close(code=resultado)
        return None

    usuario_id, conversa_id_real = resultado
    await websocket.send_json(
        {
            "tipo": "autenticado",
            "conversa_id": conversa_id_real,
            "usuario_id": usuario_id,
        }
    )
    return usuario_id, conversa_id_real


@router.websocket("/conversas/{conversa_id}/ws")
async def websocket_conversa(
    websocket: WebSocket,
    conversa_id: int,
    token: str | None = Query(default=None),
    auth: Literal["query", "mensagem"] = Query(default="query"),
) -> None:
    """Canal em tempo real da conversa com sessão curta de banco por operação."""
    if auth == "mensagem":
        await websocket.accept()
        autenticado = await _autenticar_primeira_mensagem(websocket, conversa_id)
        if autenticado is None:
            return
        usuario_id, conversa_id_real = autenticado
    else:
        credencial = token or _extrair_bearer(websocket)
        resultado = _autenticar_e_autorizar_ws(websocket, conversa_id, credencial)
        if isinstance(resultado, int):
            await websocket.close(code=resultado)
            return
        usuario_id, conversa_id_real = resultado
        await websocket.accept()

    gerenciador_conexoes.registrar(conversa_id_real, websocket)
    try:
        while True:
            try:
                dados = await websocket.receive_json()
            except ValueError:
                await websocket.send_json(
                    {
                        "tipo": "erro",
                        "codigo": "formato_invalido",
                        "mensagem": "Envie um objeto JSON válido.",
                    }
                )
                continue

            if not isinstance(dados, dict):
                await websocket.send_json(
                    {
                        "tipo": "erro",
                        "codigo": "formato_invalido",
                        "mensagem": "Envie um objeto JSON válido.",
                    }
                )
                continue

            try:
                entrada = MensagemEntrada.model_validate(dados)
            except ValidationError:
                await websocket.send_json(
                    {
                        "tipo": "erro",
                        "codigo": "mensagem_vazia",
                        "mensagem": "A mensagem precisa ter texto ou anexo.",
                    }
                )
                continue

            carga: dict | None = None
            erro_envio: HTTPException | None = None
            fechar_nao_autenticado = False

            with _sessao_ws(websocket) as db:
                usuario = db.get(Usuario, usuario_id)
                if usuario is None or not conversas.conta_ativa_no_ws(db, usuario):
                    fechar_nao_autenticado = True
                else:
                    try:
                        conversa, vaga = conversas.exigir_participante(
                            db, conversa_id_real, usuario
                        )
                        mensagem = conversas.registrar_mensagem(
                            db,
                            conversa,
                            vaga,
                            usuario,
                            texto=entrada.texto,
                            anexo_url=entrada.anexo_url,
                        )
                        carga = conversas.montar_mensagem(mensagem).model_dump(mode="json")
                    except HTTPException as erro:
                        erro_envio = erro

            if fechar_nao_autenticado:
                await websocket.close(code=WS_CODIGO_NAO_AUTENTICADO)
                return

            if erro_envio is not None:
                detalhe_erro = erro_envio.detail if isinstance(erro_envio.detail, dict) else {}
                aviso: dict[str, object] = {
                    "tipo": "erro",
                    "codigo": detalhe_erro.get("codigo", "erro_ao_enviar"),
                    "mensagem": detalhe_erro.get("mensagem", "Não foi possível enviar."),
                }
                for campo_extra in ("tentativas", "denuncia_aberta", "vaga_estado"):
                    if campo_extra in detalhe_erro:
                        aviso[campo_extra] = detalhe_erro[campo_extra]
                await websocket.send_json(aviso)
                continue

            if carga is not None:
                await gerenciador_conexoes.transmitir(conversa_id_real, carga)
    except WebSocketDisconnect:
        pass
    finally:
        gerenciador_conexoes.desconectar(conversa_id_real, websocket)
