"""Serviço de disputa: contestação em 24h pelo contratante e decisão do admin (US61).

Regras:
- O contratante dono da vaga pode contestar o serviço dentro da janela de 24h de confirmação
  (`confirmacao_conclusao_min`) após o fim do serviço, enquanto o pagamento estiver `retido` ou
  ainda não tiver sido pago (o pagamento do serviço acontece depois da conclusão). Disputa
  sobre cobrança não paga não movimenta dinheiro: o reembolso total cancela a cobrança e os
  demais resultados reemitem a cobrança com o valor devido.
- Abrir a disputa trava a vaga em `EstadoVaga.EM_DISPUTA` (`em_disputa`), impedindo que a
  varredura das 24h (`conclusao_automatica`) ou a varredura das 72h (`liberacao_parcial`)
  movimente o pagamento.
- O administrador decide em até 48h úteis com um dos 3 resultados, usando exclusivamente as
  funções do livro de lançamentos da US39 (`app.services.pagamentos`):
  1. `reembolso_total` -> `pagamentos.estornar_integral(db, pagamento)`
  2. `reembolso_parcial` -> `pagamentos.liberar_parcial(db, pagamento, 100 - percentual_reembolso)`
  3. `pagamento_integral` -> `pagamentos.liberar_integral(db, pagamento)`
- A aba de disputas do admin inclui o histórico do chat e a lista de anexos da vaga.
"""

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from fastapi import status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.pagamentos import montar_detalhe as montar_pagamento_detalhe
from app.api.v1.schemas.disputas import (
    DecisaoDisputaEntrada,
    DisputaAdminDetalhe,
    DisputaDetalhe,
    MensagemDisputaAdmin,
)
from app.erros import erro_http
from app.models import (
    Conversa,
    Disputa,
    EstadoDisputa,
    EstadoPagamento,
    EstadoVaga,
    Mensagem,
    Pagamento,
    PerfilContratante,
    PerfilProfissional,
    Usuario,
    Vaga,
)
from app.services import configuracao, pagamentos
from app.tempo import agora, como_utc

META_DISPUTA_HORAS_UTEIS = 48

ESTADOS_DISPUTAVEIS: frozenset[EstadoPagamento] = frozenset(
    {EstadoPagamento.AGUARDANDO, EstadoPagamento.EXPIRADO, EstadoPagamento.RETIDO}
)
ESTADOS_NAO_PAGOS: frozenset[EstadoPagamento] = frozenset(
    {EstadoPagamento.AGUARDANDO, EstadoPagamento.EXPIRADO}
)

ESTADOS_CONTESTAVEIS: frozenset[EstadoVaga] = frozenset(
    {
        EstadoVaga.CONTRATADA,
        EstadoVaga.EM_EXECUCAO,
        EstadoVaga.ENCERRADA,
        EstadoVaga.CONCLUIDA,
    }
)


def _em_utc(instante: datetime | None) -> datetime | None:
    return como_utc(instante) if instante is not None else None


def calcular_horas_uteis(inicio: datetime, fim: datetime) -> float:
    """Calcula as horas úteis (segunda a sexta, excluindo sábado e domingo) entre duas datas."""
    ini = como_utc(inicio)
    ter = como_utc(fim)
    if ter <= ini:
        return 0.0

    segundos_uteis = 0.0
    cursor = ini
    while cursor < ter:
        proximo_dia = (cursor + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        limite = min(ter, proximo_dia)
        if cursor.weekday() < 5:
            segundos_uteis += (limite - cursor).total_seconds()
        cursor = limite

    return round(segundos_uteis / 3600.0, 2)


def _usuarios_da_vaga(db: Session, vaga: Vaga) -> tuple[int, int | None]:
    contratante_uid = db.scalar(
        select(PerfilContratante.usuario_id).where(PerfilContratante.id == vaga.contratante_id)
    )
    profissional_uid = None
    if vaga.profissional_id is not None:
        profissional_uid = db.scalar(
            select(PerfilProfissional.usuario_id).where(
                PerfilProfissional.id == vaga.profissional_id
            )
        )
    return int(contratante_uid), profissional_uid


def _pagamento_obrigatorio(db: Session, vaga: Vaga) -> Pagamento:
    pagamento = pagamentos.pagamento_da_vaga(db, vaga)
    if pagamento is None:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "pagamento_inexistente",
            "A vaga não possui pagamento para abrir disputa.",
        )
    return pagamento


def prazo_contestacao(db: Session, vaga: Vaga) -> datetime:
    """Limite de 24h (`confirmacao_conclusao_min`) após o fim do serviço para contestar."""
    janela_min = configuracao.get("confirmacao_conclusao_min", db=db)
    return pagamentos.fim_do_servico(vaga) + timedelta(minutes=janela_min)


def abrir_disputa(
    db: Session,
    vaga: Vaga,
    contratante: Usuario,
    *,
    descricao: str,
) -> Disputa:
    """Abre a contestação dentro das 24h de confirmação e trava a vaga em `em_disputa`."""
    pagamentos.exigir_contratante_dono(db, vaga, contratante.id)

    existente = db.scalar(select(Disputa).where(Disputa.vaga_id == vaga.id))
    if existente is not None:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "disputa_ja_existe",
            "Esta vaga já possui uma disputa registrada.",
            disputa_id=existente.id,
            estado_atual=existente.estado.value,
        )

    if vaga.estado not in ESTADOS_CONTESTAVEIS:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "vaga_nao_contestavel",
            "A vaga não está em um estado que permita abrir disputa.",
            estado_atual=vaga.estado.value,
        )

    pagamento = pagamentos.pagamento_da_vaga(db, vaga)
    if pagamento is not None and pagamento.estado not in ESTADOS_DISPUTAVEIS:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "pagamento_nao_retido",
            "A disputa só pode ser aberta antes do pagamento ou com o valor retido.",
            estado_atual=pagamento.estado.value,
        )

    instante = agora()
    fim = pagamentos.fim_do_servico(vaga)
    if instante < fim:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "servico_nao_terminou",
            "A contestação só pode ser aberta depois do fim previsto do serviço.",
            fim_em=fim.isoformat(),
        )

    limite = prazo_contestacao(db, vaga)
    if instante > limite:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "prazo_contestacao_encerrado",
            "O prazo de 24 horas para contestar a conclusão deste serviço já encerrou.",
            prazo_em=limite.isoformat(),
        )

    if pagamento is None:
        pagamentos.emitir_cobranca(db, vaga)
    disputa = Disputa(
        vaga_id=vaga.id,
        aberta_por_id=contratante.id,
        descricao=descricao.strip(),
        estado=EstadoDisputa.ABERTA,
    )
    vaga.estado = EstadoVaga.EM_DISPUTA
    db.add(disputa)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "disputa_ja_existe",
            "Esta vaga já possui uma disputa registrada.",
        ) from None
    db.refresh(disputa)
    db.refresh(vaga)
    return disputa


def montar_detalhe(db: Session, disputa: Disputa, vaga: Vaga) -> DisputaDetalhe:
    pagamento = _pagamento_obrigatorio(db, vaga)
    return DisputaDetalhe(
        id=disputa.id,
        vaga_id=vaga.id,
        vaga_estado=vaga.estado,
        aberta_por_id=disputa.aberta_por_id,
        descricao=disputa.descricao,
        estado=disputa.estado,
        percentual_reembolso=disputa.percentual_reembolso,
        decisao_motivo=disputa.decisao_motivo,
        criado_em=_em_utc(disputa.criado_em),
        decidido_em=_em_utc(disputa.decidido_em),
        pagamento=montar_pagamento_detalhe(db, pagamento, vaga),
    )


def consultar_disputa_da_vaga(db: Session, vaga: Vaga, usuario: Usuario) -> DisputaDetalhe:
    contratante_uid, profissional_uid = _usuarios_da_vaga(db, vaga)
    if usuario.id not in (contratante_uid, profissional_uid):
        raise erro_http(
            status.HTTP_403_FORBIDDEN,
            "sem_acesso_a_disputa",
            "Somente o contratante da vaga e o profissional contratado veem a disputa.",
        )
    disputa = db.scalar(select(Disputa).where(Disputa.vaga_id == vaga.id))
    if disputa is None:
        raise erro_http(
            status.HTTP_404_NOT_FOUND,
            "disputa_nao_encontrada",
            "Esta vaga não possui disputa.",
        )
    return montar_detalhe(db, disputa, vaga)


def montar_item_admin(db: Session, disputa: Disputa) -> DisputaAdminDetalhe:
    """Monta a visão da aba de disputas do admin com chat, anexos da vaga, pagamento e tempo."""
    vaga = db.get(Vaga, disputa.vaga_id)
    if vaga is None:
        raise erro_http(
            status.HTTP_404_NOT_FOUND,
            "vaga_nao_encontrada",
            "Vaga da disputa não encontrada.",
        )
    pagamento = _pagamento_obrigatorio(db, vaga)
    contratante_uid, profissional_uid = _usuarios_da_vaga(db, vaga)

    conversa = db.scalar(select(Conversa).where(Conversa.vaga_id == vaga.id))
    mensagens: list[Mensagem] = []
    if conversa is not None:
        mensagens = list(
            db.scalars(
                select(Mensagem)
                .where(Mensagem.conversa_id == conversa.id)
                .order_by(Mensagem.id.asc())
            ).all()
        )

    chat = [
        MensagemDisputaAdmin(
            id=m.id,
            conversa_id=m.conversa_id,
            autor_id=m.autor_id,
            texto=m.texto,
            anexo_url=m.anexo_url,
            enviada_em=_em_utc(m.enviada_em),
            lida_em=_em_utc(m.lida_em),
        )
        for m in mensagens
    ]
    anexos = [m.anexo_url for m in mensagens if m.anexo_url]

    criado = como_utc(disputa.criado_em)
    fim_espera = como_utc(disputa.decidido_em) if disputa.decidido_em is not None else agora()
    espera_minutos = int(max(0.0, (fim_espera - criado).total_seconds()) // 60)
    horas_uteis = calcular_horas_uteis(criado, fim_espera)

    return DisputaAdminDetalhe(
        id=disputa.id,
        vaga_id=vaga.id,
        vaga_titulo=vaga.titulo,
        vaga_estado=vaga.estado,
        contratante_usuario_id=contratante_uid,
        profissional_usuario_id=profissional_uid,
        aberta_por_id=disputa.aberta_por_id,
        descricao=disputa.descricao,
        estado=disputa.estado,
        percentual_reembolso=disputa.percentual_reembolso,
        decisao_motivo=disputa.decisao_motivo,
        criado_em=criado,
        decidido_em=_em_utc(disputa.decidido_em),
        tempo_espera_minutos=espera_minutos,
        tempo_espera_horas_uteis=horas_uteis,
        meta_horas_uteis=META_DISPUTA_HORAS_UTEIS,
        acima_da_meta=horas_uteis > META_DISPUTA_HORAS_UTEIS,
        conversa_id=conversa.id if conversa is not None else None,
        chat=chat,
        anexos=anexos,
        pagamento=montar_pagamento_detalhe(db, pagamento, vaga),
    )


def listar_disputas_admin(
    db: Session,
    *,
    estado: EstadoDisputa | None = None,
) -> list[DisputaAdminDetalhe]:
    """Lista a aba de disputas do admin (em ordem de chegada / maior tempo em espera)."""
    consulta = select(Disputa)
    if estado is not None:
        consulta = consulta.where(Disputa.estado == estado)
    itens = db.scalars(consulta.order_by(Disputa.criado_em.asc(), Disputa.id.asc())).all()
    return [montar_item_admin(db, item) for item in itens]


def consultar_disputa_admin(db: Session, disputa_id: int) -> DisputaAdminDetalhe:
    disputa = db.get(Disputa, disputa_id)
    if disputa is None:
        raise erro_http(
            status.HTTP_404_NOT_FOUND,
            "disputa_nao_encontrada",
            "Disputa não encontrada.",
        )
    return montar_item_admin(db, disputa)


def _decidir_sem_pagamento(
    db: Session,
    disputa: Disputa,
    vaga: Vaga,
    pagamento: Pagamento,
    entrada: DecisaoDisputaEntrada,
) -> None:
    """Decide uma disputa cuja cobrança ainda não foi paga: nenhum valor está retido."""
    disputa.decisao_motivo = entrada.motivo
    disputa.decidido_em = agora()
    if entrada.decisao == EstadoDisputa.REEMBOLSO_TOTAL.value:
        disputa.estado = EstadoDisputa.REEMBOLSO_TOTAL
        disputa.percentual_reembolso = 100
        pagamento.estado = EstadoPagamento.EXPIRADO
        vaga.estado = EstadoVaga.REEMBOLSADA
        db.commit()
        return
    if entrada.decisao == EstadoDisputa.REEMBOLSO_PARCIAL.value:
        pct_reembolso = int(entrada.percentual_reembolso)
        disputa.estado = EstadoDisputa.REEMBOLSO_PARCIAL
        disputa.percentual_reembolso = pct_reembolso
        devido = _valor_devido(pagamento, 100 - pct_reembolso)
    else:
        disputa.estado = EstadoDisputa.PAGAMENTO_INTEGRAL
        disputa.percentual_reembolso = 0
        devido = _valor_devido(pagamento, 100)
    # O serviço volta a concluído e o contratante paga o valor decidido.
    vaga.estado = EstadoVaga.CONCLUIDA
    pagamentos.emitir_cobranca(db, vaga, valor=devido, renovar=True)
    db.commit()


def _valor_devido(pagamento: Pagamento, percentual_profissional: int) -> Decimal:
    return (Decimal(pagamento.valor_total) * percentual_profissional / Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def decidir_disputa(
    db: Session,
    disputa_id: int,
    entrada: DecisaoDisputaEntrada,
) -> DisputaAdminDetalhe:
    """Executa um dos 3 resultados da disputa usando apenas as funções do ledger da US39."""
    disputa = db.get(Disputa, disputa_id)
    if disputa is None:
        raise erro_http(
            status.HTTP_404_NOT_FOUND,
            "disputa_nao_encontrada",
            "Disputa não encontrada.",
        )
    if disputa.estado != EstadoDisputa.ABERTA:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "disputa_ja_decidida",
            "Esta disputa já foi decidida.",
            estado_atual=disputa.estado.value,
        )

    vaga = db.get(Vaga, disputa.vaga_id)
    if vaga is None:
        raise erro_http(
            status.HTTP_404_NOT_FOUND,
            "vaga_nao_encontrada",
            "Vaga da disputa não encontrada.",
        )
    pagamento = _pagamento_obrigatorio(db, vaga)

    if pagamento.estado in ESTADOS_NAO_PAGOS:
        _decidir_sem_pagamento(db, disputa, vaga, pagamento, entrada)
    elif entrada.decisao == EstadoDisputa.REEMBOLSO_TOTAL.value:
        disputa.estado = EstadoDisputa.REEMBOLSO_TOTAL
        disputa.percentual_reembolso = 100
        disputa.decisao_motivo = entrada.motivo
        disputa.decidido_em = agora()
        pagamentos.estornar_integral(db, pagamento)
    elif entrada.decisao == EstadoDisputa.REEMBOLSO_PARCIAL.value:
        pct_reembolso = int(entrada.percentual_reembolso)
        pct_profissional = 100 - pct_reembolso
        disputa.estado = EstadoDisputa.REEMBOLSO_PARCIAL
        disputa.percentual_reembolso = pct_reembolso
        disputa.decisao_motivo = entrada.motivo
        disputa.decidido_em = agora()
        pagamentos.liberar_parcial(db, pagamento, pct_profissional)
    else:
        disputa.estado = EstadoDisputa.PAGAMENTO_INTEGRAL
        disputa.percentual_reembolso = 0
        disputa.decisao_motivo = entrada.motivo
        disputa.decidido_em = agora()
        pagamentos.liberar_integral(db, pagamento)

    db.refresh(disputa)
    db.refresh(vaga)
    db.refresh(pagamento)
    return montar_item_admin(db, disputa)
