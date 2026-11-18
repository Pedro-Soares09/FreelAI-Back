"""Rotas REST e WebSocket de conversas e mensagens (regra FLX07).

A conversa nasce no aceite do convite (1 por vaga). Como o navegador não envia cabeçalhos no
handshake do WebSocket, o token JWT vem na query string (`?token=...`) e é validado antes do
`accept()`; qualquer falha fecha a conexão com código 4401.
"""

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
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


@router.post("/conversas/{conversa_id}/visto", response_model=ResultadoVisto)
def marcar_conversa_como_vista(
    conversa_id: int,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> ResultadoVisto:
    """Marca como vistas as mensagens enviadas pela outra parte na conversa."""
    return conversas.marcar_visto(db, conversa_id, usuario)


@router.websocket("/conversas/{conversa_id}/ws")
async def websocket_conversa(
    websocket: WebSocket,
    conversa_id: int,
    token: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> None:
    """Canal em tempo real da conversa. Valida JWT na query string antes do `accept()`."""
    usuario = conversas.autenticar_token_ws(db, token)
    if usuario is None:
        await websocket.close(code=WS_CODIGO_NAO_AUTENTICADO)
        return

    try:
        conversa, vaga = conversas.exigir_participante(db, conversa_id, usuario)
    except HTTPException as erro:
        codigo = (
            WS_CODIGO_NAO_ENCONTRADA
            if erro.status_code == status.HTTP_404_NOT_FOUND
            else WS_CODIGO_SEM_ACESSO
        )
        await websocket.close(code=codigo)
        return

    await websocket.accept()
    gerenciador_conexoes.registrar(conversa.id, websocket)
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

            db.refresh(conversa)
            db.refresh(vaga)
            try:
                mensagem = conversas.registrar_mensagem(
                    db,
                    conversa,
                    vaga,
                    usuario,
                    texto=entrada.texto,
                    anexo_url=entrada.anexo_url,
                )
            except HTTPException as erro:
                detalhe_erro = erro.detail if isinstance(erro.detail, dict) else {}
                await websocket.send_json(
                    {
                        "tipo": "erro",
                        "codigo": detalhe_erro.get("codigo", "erro_ao_enviar"),
                        "mensagem": detalhe_erro.get("mensagem", "Não foi possível enviar."),
                    }
                )
                continue

            detalhe = conversas.montar_mensagem(mensagem)
            await gerenciador_conexoes.transmitir(
                conversa.id,
                detalhe.model_dump(mode="json"),
            )
    except WebSocketDisconnect:
        pass
    finally:
        gerenciador_conexoes.desconectar(conversa.id, websocket)
