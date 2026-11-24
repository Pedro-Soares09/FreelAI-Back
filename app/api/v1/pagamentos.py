"""Rotas do pagamento Pix: abertura da cobrança, consulta, conclusão e validação facial."""

from decimal import Decimal

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
from app.models import Pagamento, TipoLancamento, TipoPerfil, Usuario, Vaga
from app.services import pagamentos
from app.tempo import como_utc

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


def _em_utc(instante):
    return como_utc(instante) if instante is not None else None


def _divisao(db: Session, pagamento: Pagamento) -> DivisaoValores:
    """Divisão prevista ou, quando o pagamento já foi encerrado, a que o livro registrou."""
    percentual = pagamentos.taxa_configurada(db)
    lancamentos = pagamento.lancamentos
    if pagamento.estado in pagamentos.ESTADOS_FINAIS and lancamentos:
        liquido = sum(
            (
                item.valor
                for item in lancamentos
                if item.tipo == TipoLancamento.LIBERACAO_PROFISSIONAL
            ),
            Decimal("0.00"),
        )
        taxa = sum(
            (item.valor for item in lancamentos if item.tipo == TipoLancamento.TAXA_PLATAFORMA),
            Decimal("0.00"),
        )
    else:
        liquido, taxa = pagamentos.calcular_divisao(pagamento.valor_total, percentual)
    return DivisaoValores(
        valor_total=pagamento.valor_total,
        percentual_taxa=percentual,
        taxa_plataforma=taxa,
        valor_liquido_profissional=liquido,
    )


def montar_detalhe(db: Session, pagamento: Pagamento, vaga: Vaga) -> PagamentoDetalhe:
    return PagamentoDetalhe(
        id=pagamento.id,
        vaga_id=vaga.id,
        vaga_estado=vaga.estado,
        estado=pagamento.estado,
        codigo_pix=pagamento.codigo_pix,
        expira_em=_em_utc(pagamento.expira_em),
        divisao=_divisao(db, pagamento),
        saldo_retido=pagamentos.saldo_retido(pagamento),
        lancamentos=[
            LancamentoResumo(
                id=item.id,
                tipo=item.tipo,
                valor=item.valor,
                descricao=item.descricao,
                criado_em=_em_utc(item.criado_em),
            )
            for item in sorted(pagamento.lancamentos, key=lambda item: item.id)
        ],
        criado_em=_em_utc(pagamento.criado_em),
        atualizado_em=_em_utc(pagamento.atualizado_em),
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
    pagamentos.exigir_aceites_do_acordo(db, vaga)
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
    usuario: Usuario = Depends(contratante_ativo),
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


@router.post("/vagas/{vaga_id}/falta", response_model=PagamentoDetalhe)
def registrar_falta(
    vaga_id: int,
    usuario: Usuario = Depends(contratante_ativo),
    db: Session = Depends(get_db),
) -> PagamentoDetalhe:
    """O contratante registra a falta do profissional após a tolerância; o valor é estornado."""
    vaga = _buscar_vaga(db, vaga_id)
    pagamentos.exigir_contratante_dono(db, vaga, usuario.id)
    vaga = pagamentos.registrar_falta(db, vaga)
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
