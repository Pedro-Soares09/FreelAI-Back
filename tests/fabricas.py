"""Funções auxiliares para montar dados de teste."""

from datetime import date

from sqlalchemy.orm import Session

from app.models import (
    EstadoConta,
    PapelUsuario,
    PerfilContratante,
    PerfilProfissional,
    StatusAprovacao,
    TipoPerfil,
    TipoPessoa,
    Usuario,
)
from app.security import create_access_token, hash_password

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
