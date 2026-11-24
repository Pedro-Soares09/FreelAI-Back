"""Plano Pro simulado, aceite de termos versionados e exclusão de conta (LGPD)."""

import secrets
from datetime import timedelta
from decimal import Decimal

from fastapi import status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.erros import erro_http
from app.models import (
    AceiteTermos,
    AssinaturaPro,
    EstadoConta,
    EstadoVaga,
    FinalidadeAceite,
    PerfilProfissional,
    Usuario,
    Vaga,
)
from app.security import hash_password, verify_password
from app.services import configuracao
from app.services.varreduras import varredura
from app.tempo import agora, como_utc

VERSAO_TERMOS_ATUAL = "1.0"

# Vagas em que a conta ainda tem compromisso e, por isso, não pode ser excluída.
ESTADOS_COM_COMPROMISSO = (
    EstadoVaga.AGUARDANDO_ACEITE,
    EstadoVaga.ACEITA,
    EstadoVaga.AGUARDANDO_PAGAMENTO,
    EstadoVaga.CONTRATADA,
    EstadoVaga.EM_EXECUCAO,
    EstadoVaga.ENCERRADA,
    EstadoVaga.EM_DISPUTA,
)


# ---------------------------------------------------------------- plano Pro


def _perfil_profissional(usuario: Usuario):
    if usuario.perfil_profissional is None:
        raise erro_http(
            status.HTTP_403_FORBIDDEN,
            "perfil_ativo_incorreto",
            "O plano Pro é exclusivo de profissionais.",
        )
    return usuario.perfil_profissional


def _vigente(assinatura: AssinaturaPro | None) -> bool:
    return assinatura is not None and como_utc(assinatura.valido_ate) > agora()


def situacao_pro(db: Session, usuario: Usuario) -> dict:
    perfil = _perfil_profissional(usuario)
    assinatura = db.scalar(select(AssinaturaPro).where(AssinaturaPro.profissional_id == perfil.id))
    valor = (
        float(assinatura.valor_mensal)
        if assinatura
        else float(configuracao.get("plano_pro_valor", db=db))
    )
    return {
        "ativo": _vigente(assinatura),
        "valor_mensal": valor,
        "valido_ate": como_utc(assinatura.valido_ate) if assinatura else None,
        "renovacao_automatica": bool(assinatura and assinatura.renovacao_automatica),
    }


def assinar_pro(db: Session, usuario: Usuario) -> dict:
    perfil = _perfil_profissional(usuario)
    assinatura = db.scalar(select(AssinaturaPro).where(AssinaturaPro.profissional_id == perfil.id))
    dias = configuracao.get("plano_pro_dias", db=db)
    valor = Decimal(str(configuracao.get("plano_pro_valor", db=db))).quantize(Decimal("0.01"))
    if _vigente(assinatura) and assinatura.renovacao_automatica:
        raise erro_http(status.HTTP_409_CONFLICT, "assinatura_ativa", "O plano Pro já está ativo.")
    inicio = agora()
    if assinatura is None:
        assinatura = AssinaturaPro(profissional_id=perfil.id, valor_mensal=valor, valido_ate=inicio)
        db.add(assinatura)
    base = como_utc(assinatura.valido_ate) if _vigente(assinatura) else inicio
    assinatura.valor_mensal = valor
    assinatura.iniciada_em = inicio
    assinatura.valido_ate = base + timedelta(days=dias)
    assinatura.renovacao_automatica = True
    perfil.pro = True
    db.commit()
    return situacao_pro(db, usuario)


def cancelar_pro(db: Session, usuario: Usuario) -> dict:
    perfil = _perfil_profissional(usuario)
    assinatura = db.scalar(select(AssinaturaPro).where(AssinaturaPro.profissional_id == perfil.id))
    if not _vigente(assinatura):
        raise erro_http(status.HTTP_409_CONFLICT, "assinatura_inativa", "Não há plano Pro ativo.")
    assinatura.renovacao_automatica = False
    db.commit()
    return situacao_pro(db, usuario)


@varredura("expiracao_plano_pro")
def renovar_ou_expirar_assinaturas(db: Session) -> int:
    """Renova as assinaturas vencidas com renovação automática e encerra as demais."""
    vencidas = db.scalars(select(AssinaturaPro).where(AssinaturaPro.valido_ate <= agora())).all()
    dias = configuracao.get("plano_pro_dias", db=db)
    alteradas = 0
    for assinatura in vencidas:
        perfil = db.get(PerfilProfissional, assinatura.profissional_id)
        if assinatura.renovacao_automatica:
            assinatura.valido_ate = como_utc(assinatura.valido_ate) + timedelta(days=dias)
            alteradas += 1
        elif perfil.pro:
            perfil.pro = False
            alteradas += 1
    if alteradas:
        db.commit()
    return alteradas


# ---------------------------------------------------------------- termos


def situacao_termos(db: Session, usuario: Usuario) -> dict:
    aceito = db.scalar(
        select(AceiteTermos.id).where(
            AceiteTermos.usuario_id == usuario.id,
            AceiteTermos.finalidade == FinalidadeAceite.TERMOS,
            AceiteTermos.versao == VERSAO_TERMOS_ATUAL,
        )
    )
    return {"versao_atual": VERSAO_TERMOS_ATUAL, "aceito": aceito is not None}


def aceitar_termos(db: Session, usuario: Usuario, versao: str) -> dict:
    if versao != VERSAO_TERMOS_ATUAL:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "versao_desatualizada",
            "Esta não é a versão atual dos termos.",
            versao_atual=VERSAO_TERMOS_ATUAL,
        )
    if not situacao_termos(db, usuario)["aceito"]:
        db.add(
            AceiteTermos(usuario_id=usuario.id, finalidade=FinalidadeAceite.TERMOS, versao=versao)
        )
        db.commit()
    return situacao_termos(db, usuario)


# ---------------------------------------------------------------- exclusão de conta


def excluir_conta(db: Session, usuario: Usuario, senha: str) -> None:
    """Anonimiza os dados pessoais e bloqueia o acesso; o histórico financeiro permanece."""
    if not verify_password(senha, usuario.senha_hash):
        raise erro_http(status.HTTP_403_FORBIDDEN, "senha_incorreta", "Senha incorreta.")
    contratante = usuario.perfil_contratante
    profissional = usuario.perfil_profissional
    consulta = select(Vaga.id).where(Vaga.estado.in_(ESTADOS_COM_COMPROMISSO))
    filtros = []
    if contratante is not None:
        filtros.append(Vaga.contratante_id == contratante.id)
    if profissional is not None:
        filtros.append(Vaga.profissional_id == profissional.id)
    if filtros:
        if db.scalar(consulta.where(or_(*filtros)).limit(1)) is not None:
            raise erro_http(
                status.HTTP_409_CONFLICT,
                "conta_com_compromissos",
                "Conclua ou cancele as vagas em andamento antes de excluir a conta.",
            )
    usuario.nome = "Conta excluída"
    usuario.email = f"excluida-{usuario.id}@excluida.invalid"
    usuario.senha_hash = hash_password(secrets.token_urlsafe(24))
    usuario.excluida_em = agora()
    usuario.estado = EstadoConta.BANIDA
    if profissional is not None:
        profissional.cpf = f"x{usuario.id:010d}"[-11:]
        profissional.telefone = ""
        profissional.foto_url = None
        profissional.disponivel_ate = None
        profissional.pro = False
    if contratante is not None:
        contratante.documento = None
        contratante.razao_social = None
        contratante.telefone = ""
        contratante.selfie_url = None
    db.commit()
