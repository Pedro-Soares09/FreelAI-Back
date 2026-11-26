"""Suspensão e banimento de contas: aplicação, expiração, contestação e decisão da administração.

Toda sanção fica registrada em `SancaoConta` com origem e motivo. A suspensão tem data de retorno
e é encerrada pela varredura; o banimento é permanente. Cada sanção aceita uma única contestação,
dentro do prazo configurado, decidida pela administração.
"""

from datetime import timedelta

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.erros import erro_http
from app.models import Avaliacao, ContestacaoConta, EstadoConta, SancaoConta, Usuario
from app.services import configuracao
from app.services.varreduras import varredura
from app.tempo import agora, como_utc


def sancao_vigente(db: Session, usuario_id: int) -> SancaoConta | None:
    return db.scalar(
        select(SancaoConta)
        .where(SancaoConta.usuario_id == usuario_id, SancaoConta.ativa.is_(True))
        .order_by(SancaoConta.id.desc())
    )


def _encerrar_vigentes(db: Session, usuario_id: int) -> None:
    for sancao in db.scalars(
        select(SancaoConta).where(SancaoConta.usuario_id == usuario_id, SancaoConta.ativa.is_(True))
    ):
        sancao.ativa = False
        sancao.encerrada_em = agora()


def suspender(
    db: Session,
    usuario: Usuario,
    *,
    dias: int,
    motivo: str,
    origem: str,
    commit: bool = True,
) -> SancaoConta:
    """Suspende a conta por `dias`. Uma conta banida não passa a suspensa."""
    if usuario.estado == EstadoConta.BANIDA:
        raise erro_http(status.HTTP_409_CONFLICT, "conta_banida", "A conta já está banida.")
    _encerrar_vigentes(db, usuario.id)
    inicio = agora()
    sancao = SancaoConta(
        usuario_id=usuario.id,
        tipo="suspensao",
        origem=origem,
        motivo=motivo[:255],
        inicio_em=inicio,
        fim_em=inicio + timedelta(days=dias),
    )
    db.add(sancao)
    usuario.estado = EstadoConta.SUSPENSA
    usuario.suspensa_ate = sancao.fim_em
    if commit:
        db.commit()
        db.refresh(sancao)
    return sancao


def banir(
    db: Session, usuario: Usuario, *, motivo: str, origem: str, commit: bool = True
) -> SancaoConta:
    _encerrar_vigentes(db, usuario.id)
    sancao = SancaoConta(
        usuario_id=usuario.id,
        tipo="banimento",
        origem=origem,
        motivo=motivo[:255],
        inicio_em=agora(),
    )
    db.add(sancao)
    usuario.estado = EstadoConta.BANIDA
    usuario.suspensa_ate = None
    if commit:
        db.commit()
        db.refresh(sancao)
    return sancao


def reativar(db: Session, usuario: Usuario, *, commit: bool = True) -> None:
    """Devolve a conta ao estado ativo e encerra a sanção vigente."""
    _encerrar_vigentes(db, usuario.id)
    usuario.estado = EstadoConta.ATIVA
    usuario.suspensa_ate = None
    if commit:
        db.commit()


def verificar_nota_baixa(db: Session, usuario: Usuario) -> SancaoConta | None:
    """Suspende a conta quando a nota média visível fica abaixo do mínimo configurado."""
    if usuario.estado != EstadoConta.ATIVA:
        return None
    instante = agora()
    media, total = db.execute(
        select(func.avg(Avaliacao.nota), func.count(Avaliacao.id)).where(
            Avaliacao.avaliado_id == usuario.id,
            Avaliacao.visivel_em.is_not(None),
            Avaliacao.visivel_em <= instante,
        )
    ).one()
    minimo = int(configuracao.get("avaliacoes_minimas_suspensao", db=db))
    limite = float(configuracao.get("nota_minima_suspensao", db=db))
    if media is None or int(total) < minimo or float(media) >= limite:
        return None
    return suspender(
        db,
        usuario,
        dias=int(configuracao.get("suspensao_nota_dias", db=db)),
        motivo=f"Nota média {float(media):.1f} em {int(total)} avaliações.",
        origem="nota",
    )


@varredura("expiracao_suspensoes")
def encerrar_suspensoes_vencidas(db: Session) -> int:
    """Reativa as contas cuja suspensão já passou da data de retorno."""
    vencidas = db.scalars(
        select(Usuario).where(
            Usuario.estado == EstadoConta.SUSPENSA,
            Usuario.suspensa_ate.is_not(None),
            Usuario.suspensa_ate <= agora(),
        )
    ).all()
    for usuario in vencidas:
        reativar(db, usuario, commit=False)
    if vencidas:
        db.commit()
    return len(vencidas)


def _contestacao_da(db: Session, sancao: SancaoConta) -> ContestacaoConta | None:
    return db.scalar(select(ContestacaoConta).where(ContestacaoConta.sancao_id == sancao.id))


def prazo_da_contestacao(db: Session, sancao: SancaoConta):
    dias = int(configuracao.get("contestacao_prazo_dias", db=db))
    return como_utc(sancao.inicio_em) + timedelta(days=dias)


def situacao(db: Session, usuario: Usuario) -> dict:
    """Sanção vigente, prazo e andamento da contestação, para a tela da conta bloqueada."""
    sancao = sancao_vigente(db, usuario.id)
    if sancao is None:
        return {"estado": usuario.estado.value, "sancao": None, "contestacao": None}
    contestacao = _contestacao_da(db, sancao)
    prazo = prazo_da_contestacao(db, sancao)
    return {
        "estado": usuario.estado.value,
        "sancao": {
            "id": sancao.id,
            "tipo": sancao.tipo,
            "motivo": sancao.motivo,
            "inicio_em": como_utc(sancao.inicio_em),
            "retorno_em": como_utc(sancao.fim_em) if sancao.fim_em else None,
            "contestavel_ate": prazo,
            "pode_contestar": contestacao is None and agora() <= prazo,
        },
        "contestacao": None
        if contestacao is None
        else {
            "texto": contestacao.texto,
            "criada_em": como_utc(contestacao.criada_em),
            "decidida_em": como_utc(contestacao.decidida_em) if contestacao.decidida_em else None,
            "aceita": contestacao.aceita,
            "motivo_decisao": contestacao.motivo_decisao,
        },
    }


def contestar(db: Session, usuario: Usuario, texto: str) -> ContestacaoConta:
    sancao = sancao_vigente(db, usuario.id)
    if sancao is None:
        raise erro_http(
            status.HTTP_409_CONFLICT, "sem_sancao", "Esta conta não tem sanção para contestar."
        )
    if _contestacao_da(db, sancao) is not None:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "contestacao_ja_enviada",
            "Cada caso aceita uma única contestação.",
        )
    if agora() > prazo_da_contestacao(db, sancao):
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "prazo_de_contestacao_encerrado",
            "O prazo para contestar este caso terminou.",
        )
    contestacao = ContestacaoConta(sancao_id=sancao.id, usuario_id=usuario.id, texto=texto.strip())
    db.add(contestacao)
    db.commit()
    db.refresh(contestacao)
    return contestacao


def decidir_contestacao(
    db: Session, contestacao_id: int, *, aceita: bool, motivo: str
) -> ContestacaoConta:
    contestacao = db.get(ContestacaoConta, contestacao_id)
    if contestacao is None:
        raise erro_http(
            status.HTTP_404_NOT_FOUND, "contestacao_nao_encontrada", "Contestação não encontrada."
        )
    if contestacao.decidida_em is not None:
        raise erro_http(
            status.HTTP_409_CONFLICT, "contestacao_ja_decidida", "Esta contestação já foi decidida."
        )
    contestacao.aceita = aceita
    contestacao.motivo_decisao = motivo.strip()[:255]
    contestacao.decidida_em = agora()
    if aceita:
        usuario = db.get(Usuario, contestacao.usuario_id)
        reativar(db, usuario, commit=False)
    db.commit()
    db.refresh(contestacao)
    return contestacao
