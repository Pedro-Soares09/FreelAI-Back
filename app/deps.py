from collections.abc import Callable

from fastapi import Depends, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.erros import erro_http
from app.models import EstadoConta, PapelUsuario, SancaoConta, TipoPerfil, Usuario
from app.security import decode_access_token
from app.tempo import agora, como_utc

esquema_bearer = HTTPBearer(auto_error=False, description="Token de acesso obtido no login.")


def _nao_autenticado():
    erro = erro_http(
        status.HTTP_401_UNAUTHORIZED,
        "nao_autenticado",
        "Credenciais inválidas ou token expirado.",
    )
    erro.headers = {"WWW-Authenticate": "Bearer"}
    return erro


def _motivo_da_sancao(usuario: Usuario, db: Session) -> str | None:
    return db.scalar(
        select(SancaoConta.motivo)
        .where(SancaoConta.usuario_id == usuario.id, SancaoConta.ativa.is_(True))
        .order_by(SancaoConta.id.desc())
    )


def barrar_conta_bloqueada(usuario: Usuario, db: Session) -> None:
    """Impede o uso de contas banidas ou suspensas. Suspensões vencidas são encerradas."""
    if usuario.excluida_em is not None:
        raise erro_http(status.HTTP_403_FORBIDDEN, "conta_excluida", "Esta conta foi excluída.")
    if usuario.estado == EstadoConta.BANIDA:
        raise erro_http(
            status.HTTP_403_FORBIDDEN,
            "conta_banida",
            "Esta conta foi banida.",
            motivo=_motivo_da_sancao(usuario, db),
        )
    if usuario.estado == EstadoConta.SUSPENSA:
        retorno = usuario.suspensa_ate and como_utc(usuario.suspensa_ate)
        if retorno is not None and retorno <= agora():
            usuario.estado = EstadoConta.ATIVA
            usuario.suspensa_ate = None
            db.commit()
            return
        raise erro_http(
            status.HTTP_403_FORBIDDEN,
            "conta_suspensa",
            "Esta conta está suspensa.",
            retorno_em=retorno.isoformat() if retorno else None,
            motivo=_motivo_da_sancao(usuario, db),
        )


def usuario_atual(
    credenciais: HTTPAuthorizationCredentials | None = Depends(esquema_bearer),
    db: Session = Depends(get_db),
) -> Usuario:
    """Usuário dono do token de acesso. Contas banidas e suspensas são barradas."""
    if credenciais is None:
        raise _nao_autenticado()
    try:
        carga = decode_access_token(credenciais.credentials)
        usuario_id = int(carga["sub"])
    except (JWTError, KeyError, ValueError):
        raise _nao_autenticado() from None

    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        raise _nao_autenticado()
    barrar_conta_bloqueada(usuario, db)
    return usuario


def usuario_opcional(
    credenciais: HTTPAuthorizationCredentials | None = Depends(esquema_bearer),
    db: Session = Depends(get_db),
) -> Usuario | None:
    """Devolve o usuário autenticado quando há Bearer token, ou `None` quando não há."""
    if credenciais is None:
        return None
    return usuario_atual(credenciais, db)


# Nome mantido para as rotas anteriores à versão 1.
get_current_user = usuario_atual


def exige_conta_ativa(usuario: Usuario = Depends(usuario_atual)) -> Usuario:
    """Contas pendentes de aprovação não podem publicar vagas nem aceitar convites."""
    if usuario.estado != EstadoConta.ATIVA:
        raise erro_http(
            status.HTTP_403_FORBIDDEN,
            "conta_pendente",
            "A conta ainda não foi aprovada.",
        )
    return usuario


def exige_perfil(tipo: TipoPerfil) -> Callable[..., Usuario]:
    """Dependência que exige que o perfil ativo do usuário seja `tipo` e esteja cadastrado."""

    def dependencia(usuario: Usuario = Depends(usuario_atual)) -> Usuario:
        cadastrado = (
            usuario.perfil_profissional
            if tipo == TipoPerfil.PROFISSIONAL
            else usuario.perfil_contratante
        )
        if usuario.perfil_ativo != tipo or cadastrado is None:
            raise erro_http(
                status.HTTP_403_FORBIDDEN,
                "perfil_ativo_incorreto",
                f"Esta ação exige o perfil {tipo.value} ativo.",
                perfil_necessario=tipo.value,
            )
        return usuario

    return dependencia


def exige_admin(usuario: Usuario = Depends(usuario_atual)) -> Usuario:
    if usuario.papel != PapelUsuario.ADMIN:
        raise erro_http(
            status.HTTP_403_FORBIDDEN,
            "acesso_restrito",
            "Esta ação é restrita à administração.",
        )
    return usuario
