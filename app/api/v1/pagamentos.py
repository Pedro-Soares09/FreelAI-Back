"""Rotas do pagamento Pix: abertura da cobrança, consulta, conclusão e validação facial."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.v1.schemas.pagamentos import (
    DivisaoValores,
    LancamentoResumo,
    PagamentoDetalhe,
    ValidacaoFacialEntrada,
)
from app.config import settings
from app.db import get_db
from app.deps import exige_conta_ativa, exige_perfil, usuario_atual
from app.erros import erro_http
from app.models import Pagamento, TipoPerfil, Usuario, Vaga
from app.services import pagamentos

router = APIRouter(tags=["pagamentos"])


def contratante_ativo(
    usuario: Usuario = Depends(exige_perfil(TipoPerfil.CONTRATANTE)),
    _: Usuario = Depends(exige_conta_ativa),
) -> Usuario:
    return usuario


def _buscar_vaga(db: Session, vaga_id: int) -> Vaga:
    vaga = db.get(Vaga, vaga_id)
    if vaga is None:
        raise erro_http(status.HTTP_404_NOT_FOUND, "vaga_nao_encontrada", "Vaga não encontrada.")
    return vaga


def _buscar_pagamento(db: Session, pagamento_id: int) -> Pagamento:
    pagamento = db.get(Pagamento, pagamento_id)
    if pagamento is None:
        raise erro_http(
            status.HTTP_404_NOT_FOUND, "pagamento_nao_encontrado", "Pagamento não encontrado."
        )
    return pagamento


def _pagamento_obrigatorio(db: Session, vaga: Vaga) -> Pagamento:
    pagamento = pagamentos.pagamento_da_vaga(db, vaga)
    if pagamento is None:
        raise erro_http(
            status.HTTP_404_NOT_FOUND,
            "pagamento_nao_encontrado",
            "Esta vaga ainda não possui cobrança.",
        )
    return pagamento


def _e_parte_da_vaga(usuario: Usuario, vaga: Vaga) -> bool:
    contratante = usuario.perfil_contratante
    profissional = usuario.perfil_profissional
    return (contratante is not None and contratante.id == vaga.contratante_id) or (
        profissional is not None
        and vaga.profissional_id is not None
        and profissional.id == vaga.profissional_id
    )


def montar_detalhe(db: Session, pagamento: Pagamento, vaga: Vaga) -> PagamentoDetalhe:
    percentual = pagamentos.taxa_configurada(db)
    liquido, taxa = pagamentos.calcular_divisao(pagamento.valor_total, percentual)
    return PagamentoDetalhe(
        id=pagamento.id,
        vaga_id=vaga.id,
        vaga_estado=vaga.estado,
        estado=pagamento.estado,
        codigo_pix=pagamento.codigo_pix,
        expira_em=pagamento.expira_em,
        divisao=DivisaoValores(
            valor_total=pagamento.valor_total,
            percentual_taxa=percentual,
            taxa_plataforma=taxa,
            valor_liquido_profissional=liquido,
        ),
        saldo_retido=pagamentos.saldo_retido(pagamento),
        lancamentos=[
            LancamentoResumo.model_validate(item)
            for item in sorted(pagamento.lancamentos, key=lambda item: item.id)
        ],
        criado_em=pagamento.criado_em,
        atualizado_em=pagamento.atualizado_em,
    )


@router.post(
    "/vagas/{vaga_id}/pagamento",
    response_model=PagamentoDetalhe,
    status_code=status.HTTP_201_CREATED,
)
def abrir_cobranca(
    vaga_id: int,
    usuario: Usuario = Depends(contratante_ativo),
    db: Session = Depends(get_db),
) -> PagamentoDetalhe:
    """Abre a cobrança Pix da vaga aceita. O valor fica retido até a conclusão do serviço."""
    vaga = _buscar_vaga(db, vaga_id)
    pagamentos.exigir_contratante_dono(db, vaga, usuario.id)
    pagamento = pagamentos.criar_pagamento(db, vaga)
    return montar_detalhe(db, pagamento, vaga)


@router.get("/vagas/{vaga_id}/pagamento", response_model=PagamentoDetalhe)
def consultar_pagamento(
    vaga_id: int,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> PagamentoDetalhe:
    """Estado da cobrança, divisão entre profissional e plataforma e lançamentos."""
    vaga = _buscar_vaga(db, vaga_id)
    if not _e_parte_da_vaga(usuario, vaga):
        raise erro_http(
            status.HTTP_403_FORBIDDEN,
            "sem_acesso_ao_pagamento",
            "Somente o contratante da vaga e o profissional contratado veem o pagamento.",
        )
    pagamento = _pagamento_obrigatorio(db, vaga)
    return montar_detalhe(db, pagamento, vaga)


@router.post("/pagamentos/{pagamento_id}/simular-confirmacao", response_model=PagamentoDetalhe)
def simular_confirmacao(
    pagamento_id: int,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> PagamentoDetalhe:
    """Simula a confirmação do Pix pelo provedor. Disponível apenas no perfil de demonstração."""
    if settings.perfil != "demo":
        raise erro_http(status.HTTP_404_NOT_FOUND, "rota_indisponivel", "Rota não encontrada.")
    pagamento = _buscar_pagamento(db, pagamento_id)
    vaga = _buscar_vaga(db, pagamento.vaga_id)
    pagamentos.exigir_contratante_dono(db, vaga, usuario.id)
    pagamento = pagamentos.reter(db, pagamento)
    return montar_detalhe(db, pagamento, vaga)


@router.post("/vagas/{vaga_id}/conclusao", response_model=PagamentoDetalhe)
def confirmar_conclusao(
    vaga_id: int,
    usuario: Usuario = Depends(contratante_ativo),
    db: Session = Depends(get_db),
) -> PagamentoDetalhe:
    """O contratante confirma a conclusão do serviço depois do fim previsto."""
    vaga = _buscar_vaga(db, vaga_id)
    vaga = pagamentos.confirmar_conclusao(db, vaga, usuario.id)
    return montar_detalhe(db, _pagamento_obrigatorio(db, vaga), vaga)


@router.post("/vagas/{vaga_id}/validacao-facial", response_model=PagamentoDetalhe)
def validar_facial(
    vaga_id: int,
    entrada: ValidacaoFacialEntrada | None = None,
    usuario: Usuario = Depends(contratante_ativo),
    db: Session = Depends(get_db),
) -> PagamentoDetalhe:
    """Validação facial do contratante; aprovada, libera o valor integral ao profissional."""
    vaga = _buscar_vaga(db, vaga_id)
    selfie = entrada.selfie_url if entrada is not None else None
    pagamento = pagamentos.validar_facial_e_liberar(db, vaga, usuario, selfie_url=selfie)
    db.refresh(vaga)
    return montar_detalhe(db, pagamento, vaga)
