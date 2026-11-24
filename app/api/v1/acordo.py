"""Rotas do acordo (contrato digital): termos e aceite "Li e aceito" das duas partes."""

from decimal import Decimal

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.schemas.integracao import AceitesAcordo, DadosAcordo, PerfilResumo
from app.api.v1.vagas import reputacao_do_usuario
from app.db import get_db
from app.deps import exige_conta_ativa, usuario_atual
from app.erros import erro_http
from app.models import (
    AceiteContrato,
    Contrato,
    Endereco,
    EstadoVaga,
    PerfilContratante,
    PerfilProfissional,
    TipoPerfil,
    Usuario,
    Vaga,
)
from app.services import configuracao, match, pagamentos

router = APIRouter(tags=["acordo"])

VERSAO_TERMOS = "1.0"

# Estados em que o acordo pode ser aceito: depois do aceite do convite e antes da contratação.
ESTADOS_ACEITAVEIS = (EstadoVaga.ACEITA, EstadoVaga.AGUARDANDO_PAGAMENTO)
ESTADOS_COM_ACORDO = ESTADOS_ACEITAVEIS + (
    EstadoVaga.CONTRATADA,
    EstadoVaga.EM_EXECUCAO,
    EstadoVaga.ENCERRADA,
    EstadoVaga.CONCLUIDA,
    EstadoVaga.LIBERADA,
    EstadoVaga.LIBERADA_PARCIAL,
)


def _vaga_com_acordo(db: Session, vaga_id: int, usuario: Usuario) -> tuple[Vaga, TipoPerfil]:
    """Vaga que já tem profissional contratado e da qual o usuário é uma das partes."""
    vaga = db.get(Vaga, vaga_id)
    if vaga is None or vaga.profissional_id is None:
        raise erro_http(
            status.HTTP_404_NOT_FOUND, "acordo_nao_encontrado", "Esta vaga ainda não tem acordo."
        )
    contratante = usuario.perfil_contratante
    profissional = usuario.perfil_profissional
    if contratante is not None and contratante.id == vaga.contratante_id:
        papel = TipoPerfil.CONTRATANTE
    elif profissional is not None and profissional.id == vaga.profissional_id:
        papel = TipoPerfil.PROFISSIONAL
    else:
        raise erro_http(
            status.HTTP_404_NOT_FOUND, "acordo_nao_encontrado", "Esta vaga ainda não tem acordo."
        )
    if vaga.estado not in ESTADOS_COM_ACORDO:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "acordo_indisponivel",
            "O acordo não está disponível no estado atual da vaga.",
            estado_atual=vaga.estado.value,
        )
    return vaga, papel


def _perfil_resumo(db: Session, usuario: Usuario, verificado: bool, novo: bool) -> PerfilResumo:
    nota, total = reputacao_do_usuario(db, usuario.id)
    return PerfilResumo(
        usuario_id=usuario.id,
        nome=usuario.nome,
        verificado=verificado,
        novo=novo,
        pro=False,
        nota_media=nota if total else None,
        total_avaliacoes=total,
    )


def _horas(minutos: float) -> str:
    """Prazo legível: em horas quando passa de uma hora, em minutos nos perfis de demonstração."""
    if minutos < 60:
        return f"{minutos:g} min"
    return f"{minutos / 60:g} h"


def _dados(db: Session, vaga: Vaga) -> DadosAcordo:
    endereco = db.get(Endereco, vaga.endereco_id)
    perfil_contratante = db.get(PerfilContratante, vaga.contratante_id)
    perfil_profissional = db.get(PerfilProfissional, vaga.profissional_id)
    contratante = db.get(Usuario, perfil_contratante.usuario_id)
    profissional = db.get(Usuario, perfil_profissional.usuario_id)

    total = vaga.valor_fixado or vaga.valor_inicial
    liquido, taxa = pagamentos.calcular_divisao(Decimal(total), db=db)
    contrato = db.scalars(select(Contrato).where(Contrato.vaga_id == vaga.id)).first()
    aceitos = {a.perfil for a in contrato.aceites} if contrato else set()
    atraso = _horas(configuracao.get("cancelamento_tardio_min", db=db))
    prazo_avaliacao = _horas(configuracao.get("avaliacao_prazo_min", db=db))
    janela_conclusao = _horas(configuracao.get("confirmacao_conclusao_min", db=db))

    partes = [endereco.logradouro, endereco.numero, endereco.bairro, endereco.cidade]
    return DadosAcordo(
        vaga_id=vaga.id,
        servico=vaga.titulo,
        escopo=vaga.descricao,
        inicio_em=vaga.inicio_em,
        duracao_minutos=vaga.duracao_minutos,
        vestimenta=vaga.vestimenta,
        atribuicoes=vaga.atribuicoes,
        local_bairro=endereco.bairro,
        endereco_completo=", ".join(p for p in partes if p),
        valor_total=Decimal(total),
        taxa_plataforma=taxa,
        valor_liquido_profissional=liquido,
        condicoes_pagamento=vaga.condicoes_pagamento
        or (
            "Pix pago pelo contratante, retido pela plataforma e liberado ao profissional "
            f"após a conclusão ({janela_conclusao} para confirmar)."
        ),
        regras_cancelamento=(
            f"Cancelar com menos de {atraso} de antecedência gera advertência ao autor."
        ),
        condicoes_avaliacao=(
            f"Avaliação mútua de 1 a 5 em até {prazo_avaliacao} após a conclusão."
        ),
        versao_termos=contrato.versao_termos if contrato else VERSAO_TERMOS,
        profissional=_perfil_resumo(
            db, profissional, perfil_profissional.verificado, match.eh_novo(db, perfil_profissional)
        ),
        contratante=_perfil_resumo(db, contratante, perfil_contratante.verificado, False),
        aceites=AceitesAcordo(
            profissional=TipoPerfil.PROFISSIONAL in aceitos,
            contratante=TipoPerfil.CONTRATANTE in aceitos,
        ),
    )


@router.get("/vagas/{vaga_id}/acordo", response_model=DadosAcordo)
def consultar_acordo(
    vaga_id: int, usuario: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)
) -> DadosAcordo:
    """Termos do acordo e situação dos aceites. Restrito às duas partes da vaga."""
    vaga, _ = _vaga_com_acordo(db, vaga_id, usuario)
    return _dados(db, vaga)


@router.post("/vagas/{vaga_id}/acordo/aceite", response_model=DadosAcordo)
def aceitar_acordo(
    vaga_id: int,
    usuario: Usuario = Depends(usuario_atual),
    _: Usuario = Depends(exige_conta_ativa),
    db: Session = Depends(get_db),
) -> DadosAcordo:
    """Registra o "Li e aceito" do usuário. O contrato é congelado no primeiro aceite."""
    vaga, papel = _vaga_com_acordo(db, vaga_id, usuario)
    if vaga.estado not in ESTADOS_ACEITAVEIS:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "acordo_indisponivel",
            "O acordo não pode mais ser aceito neste estado da vaga.",
            estado_atual=vaga.estado.value,
        )
    contrato = db.scalars(select(Contrato).where(Contrato.vaga_id == vaga.id)).first()
    if contrato is None:
        dados = _dados(db, vaga)
        contrato = Contrato(
            vaga_id=vaga.id,
            versao_termos=VERSAO_TERMOS,
            conteudo=dados.model_dump(mode="json", exclude={"aceites"}),
        )
        db.add(contrato)
        db.flush()
    if not any(a.perfil == papel for a in contrato.aceites):
        db.add(AceiteContrato(contrato_id=contrato.id, usuario_id=usuario.id, perfil=papel))
        db.commit()
        db.refresh(contrato)
    return _dados(db, vaga)
