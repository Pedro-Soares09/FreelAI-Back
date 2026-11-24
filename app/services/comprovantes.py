"""Comprovantes de pagamento da vaga: serviço, horas extras e bônus.

O pagamento do serviço acontece depois da conclusão. Horas extras e bônus são combinados
diretamente entre as partes (horas extras exigem o registro do acordo prévio) e o comprovante
fica anexado à vaga. Sobre os valores adicionais a plataforma retém a taxa `taxa_adicional_pct`.
Quem paga adicionais ganha destaque no perfil de contratante, sem efeito no ranking do match.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import BinaryIO

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.schemas.comprovantes import (
    ComprovanteDetalhe,
    DestaqueContratante,
    ResumoComprovantes,
)
from app.erros import erro_http
from app.models import (
    Arquivo,
    ComprovantePagamento,
    EstadoVaga,
    PapelUsuario,
    PerfilContratante,
    PerfilProfissional,
    TipoArquivo,
    TipoComprovante,
    Usuario,
    Vaga,
)
from app.services import arquivos, configuracao
from app.tempo import como_utc

CENTAVOS = Decimal("0.01")
ADICIONAIS = (TipoComprovante.HORAS_EXTRAS, TipoComprovante.BONUS)
TAMANHO_MINIMO_ACORDO = 10

ESTADOS_COM_COMPROVANTE = (
    EstadoVaga.CONTRATADA,
    EstadoVaga.EM_EXECUCAO,
    EstadoVaga.ENCERRADA,
    EstadoVaga.CONCLUIDA,
    EstadoVaga.LIBERADA,
    EstadoVaga.LIBERADA_PARCIAL,
)


def _centavos(valor: Decimal) -> Decimal:
    return valor.quantize(CENTAVOS, rounding=ROUND_HALF_UP)


def taxa_adicional(db: Session | None = None) -> Decimal:
    return Decimal(str(configuracao.get("taxa_adicional_pct", db=db)))


def _usuarios_da_vaga(db: Session, vaga: Vaga) -> tuple[int | None, int | None]:
    contratante = db.scalar(
        select(PerfilContratante.usuario_id).where(PerfilContratante.id == vaga.contratante_id)
    )
    profissional = None
    if vaga.profissional_id is not None:
        profissional = db.scalar(
            select(PerfilProfissional.usuario_id).where(
                PerfilProfissional.id == vaga.profissional_id
            )
        )
    return contratante, profissional


def exigir_parte_ou_admin(db: Session, vaga: Vaga, usuario: Usuario) -> None:
    contratante, profissional = _usuarios_da_vaga(db, vaga)
    if usuario.id not in (contratante, profissional) and usuario.papel != PapelUsuario.ADMIN:
        raise erro_http(
            status.HTTP_403_FORBIDDEN,
            "sem_acesso_aos_comprovantes",
            "Somente as partes da vaga e a administração veem os comprovantes.",
        )


def _detalhe(item: ComprovantePagamento) -> ComprovanteDetalhe:
    return ComprovanteDetalhe(
        id=item.id,
        vaga_id=item.vaga_id,
        tipo=item.tipo,
        valor=item.valor,
        taxa_pct=item.taxa_pct,
        taxa_plataforma=item.taxa_valor,
        acordo_previo=item.acordo_previo,
        enviado_por_id=item.enviado_por_id,
        arquivo_url=f"/api/v1/vagas/{item.vaga_id}/comprovantes/{item.id}/arquivo",
        criado_em=como_utc(item.criado_em),
    )


def anexar(
    db: Session,
    vaga: Vaga,
    usuario: Usuario,
    *,
    tipo: TipoComprovante,
    valor: Decimal,
    fluxo: BinaryIO,
    nome_original: str | None,
    content_type: str | None,
    acordo_previo: str | None,
) -> ComprovanteDetalhe:
    contratante, profissional = _usuarios_da_vaga(db, vaga)
    if usuario.id not in (contratante, profissional):
        raise erro_http(
            status.HTTP_403_FORBIDDEN,
            "sem_acesso_aos_comprovantes",
            "Somente as partes da vaga podem anexar comprovantes.",
        )
    if vaga.estado not in ESTADOS_COM_COMPROVANTE:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "vaga_sem_comprovante",
            "A vaga não está em um estado que aceite comprovantes.",
            estado_atual=vaga.estado.value,
        )
    if tipo in ADICIONAIS and usuario.id != contratante:
        raise erro_http(
            status.HTTP_403_FORBIDDEN,
            "adicional_somente_contratante",
            "Somente o contratante anexa comprovantes de horas extras e bônus.",
        )
    valor = _centavos(valor)
    if valor <= 0:
        raise erro_http(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "valor_invalido",
            "O valor do comprovante precisa ser maior que zero.",
        )
    acordo = (acordo_previo or "").strip() or None
    if tipo == TipoComprovante.HORAS_EXTRAS and (
        acordo is None or len(acordo) < TAMANHO_MINIMO_ACORDO
    ):
        raise erro_http(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "acordo_previo_obrigatorio",
            "Horas extras exigem o registro do acordo prévio entre as partes.",
        )
    arquivo = arquivos.salvar_upload(
        db,
        usuario,
        TipoArquivo.COMPROVANTE,
        fluxo,
        nome_original=nome_original,
        content_type=content_type,
    )
    pct = taxa_adicional(db) if tipo in ADICIONAIS else Decimal("0")
    item = ComprovantePagamento(
        vaga_id=vaga.id,
        tipo=tipo,
        valor=valor,
        arquivo_id=arquivo.id,
        enviado_por_id=usuario.id,
        acordo_previo=acordo,
        taxa_pct=pct,
        taxa_valor=_centavos(valor * pct / Decimal("100")),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _detalhe(item)


def resumir(db: Session, vaga: Vaga) -> ResumoComprovantes:
    itens = db.scalars(
        select(ComprovantePagamento)
        .where(ComprovantePagamento.vaga_id == vaga.id)
        .order_by(ComprovantePagamento.id)
    ).all()

    def total(tipo: TipoComprovante) -> Decimal:
        return sum((i.valor for i in itens if i.tipo == tipo), Decimal("0.00"))

    horas, bonus = total(TipoComprovante.HORAS_EXTRAS), total(TipoComprovante.BONUS)
    taxa = sum((i.taxa_valor for i in itens if i.tipo in ADICIONAIS), Decimal("0.00"))
    return ResumoComprovantes(
        vaga_id=vaga.id,
        comprovantes=[_detalhe(i) for i in itens],
        total_servico=total(TipoComprovante.SERVICO),
        total_horas_extras=horas,
        total_bonus=bonus,
        total_adicional=horas + bonus,
        percentual_taxa_adicional=taxa_adicional(db),
        taxa_adicional_plataforma=taxa,
        liquido_adicional_profissional=horas + bonus - taxa,
    )


def ler_arquivo(db: Session, vaga: Vaga, comprovante_id: int) -> tuple[Arquivo, bytes]:
    item = db.get(ComprovantePagamento, comprovante_id)
    if item is None or item.vaga_id != vaga.id:
        raise erro_http(
            status.HTTP_404_NOT_FOUND, "comprovante_nao_encontrado", "Comprovante não encontrado."
        )
    registro = db.get(Arquivo, item.arquivo_id)
    conteudo = arquivos.obter_armazenamento().ler(registro.chave) if registro else None
    if registro is None or conteudo is None:
        raise erro_http(
            status.HTTP_404_NOT_FOUND, "arquivo_nao_encontrado", "Arquivo não encontrado."
        )
    return registro, conteudo


def destaque_do_contratante(db: Session, usuario_id: int) -> DestaqueContratante:
    """Quantidade e soma de adicionais (horas extras e bônus) pagos por este contratante."""
    quantidade, soma = db.execute(
        select(func.count(ComprovantePagamento.id), func.sum(ComprovantePagamento.valor)).where(
            ComprovantePagamento.enviado_por_id == usuario_id,
            ComprovantePagamento.tipo.in_(ADICIONAIS),
        )
    ).one()
    quantidade = int(quantidade or 0)
    minimo = int(configuracao.get("adicionais_para_destaque", db=db))
    return DestaqueContratante(
        adicionais_pagos=quantidade,
        total_adicional=Decimal(str(soma or 0)).quantize(CENTAVOS),
        destaque=quantidade >= minimo,
    )
