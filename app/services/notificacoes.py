"""Central de notificações no app (polling REST, Pro imediato e demais +2 min) — US62 (PAG05).

Regras:
- Sem envio de e-mail no MVP; entrega somente por polling REST na central do aplicativo.
- Toda notificação é gravada com `visivel_em`:
  - Assinante do plano **Pro**: `visivel_em = agora()` (visibilidade imediata).
  - Demais usuários: `visivel_em = agora() + timedelta(minutes=notificacao_atraso_min)`
    (padrão de 2 minutos em `producao` e 0 no perfil `demo`).
- A listagem e a contagem de não lidas filtram apenas notificações cujo `visivel_em <= agora()`,
  garantindo que quem não é Pro só veja a notificação após o atraso configurado.
- Eventos cobertos:
  1. `selecionado_top3`: profissional selecionado entre os 3 da rodada do match
  2. `convite_escolhido`: profissional escolhido pelo contratante (convite)
  3. `convite_aceito`: contratante avisado quando o profissional aceita o convite
  4. `contrato_pronto`: partes avisadas quando o contrato está pronto para aceite/pagamento
  5. `pagamento_retido`: profissional avisado que o Pix foi confirmado e retido
  6. `lembrete_confirmacao`: contratante lembrado de confirmar a conclusão após o fim do serviço
  7. `pagamento_liberado`: profissional avisado que o pagamento foi liberado
  8. `avaliacao_pendente`: partes avisadas para avaliar o serviço concluído em até 48h
  9. `denuncia_decidida`: destinatário avisado quando a administração decide uma denúncia
"""

from datetime import datetime, timedelta

from fastapi import status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.v1.schemas.notificacoes import (
    CentralNotificacoes,
    EventoNotificacao,
    NotificacaoDetalhe,
    ResultadoLeituraTodas,
)
from app.erros import erro_http
from app.models import (
    Convite,
    Denuncia,
    EstadoDenuncia,
    EstadoVaga,
    Notificacao,
    Pagamento,
    PerfilContratante,
    PerfilProfissional,
    Usuario,
    Vaga,
)
from app.services import configuracao
from app.services.varreduras import varredura
from app.tempo import agora, como_utc

# Conjunto em memória de IDs de usuários com plano Pro ativo (além de atributo `pro` no modelo
# ou perfil, quando presente). Permite ativar/testar o plano Pro sem exigir migração de tabela.
_usuarios_pro: set[int] = set()


def definir_plano_pro(usuario_id: int, ativo: bool = True) -> None:
    """Ativa ou desativa o plano Pro para `usuario_id`."""
    if ativo:
        _usuarios_pro.add(usuario_id)
    else:
        _usuarios_pro.discard(usuario_id)


def limpar_planos_pro() -> None:
    """Remove todas as marcações Pro em memória (usado no isolamento entre testes)."""
    _usuarios_pro.clear()


def usuario_eh_pro(db: Session, usuario_id: int, *, pro_explicito: bool | None = None) -> bool:
    """Verifica se o destinatário tem plano Pro ativo (visibilidade imediata das notificações)."""
    if pro_explicito is not None:
        return pro_explicito
    if usuario_id in _usuarios_pro:
        return True
    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        return False
    if bool(getattr(usuario, "pro", False)):
        return True
    if usuario.perfil_profissional is not None and bool(
        getattr(usuario.perfil_profissional, "pro", False)
    ):
        return True
    if usuario.perfil_contratante is not None and bool(
        getattr(usuario.perfil_contratante, "pro", False)
    ):
        return True
    return False


def _em_utc(instante: datetime | None) -> datetime | None:
    return como_utc(instante) if instante is not None else None


def calcular_visivel_em(
    db: Session,
    usuario_id: int,
    *,
    pro: bool | None = None,
) -> datetime:
    """Calcula `visivel_em`: imediato para Pro ou `agora() + notificacao_atraso_min` para demais."""
    instante = agora()
    if usuario_eh_pro(db, usuario_id, pro_explicito=pro):
        return instante
    atraso_min = configuracao.get("notificacao_atraso_min", db=db)
    if atraso_min <= 0:
        return instante
    return instante + timedelta(minutes=atraso_min)


def criar_notificacao(
    db: Session,
    *,
    usuario_id: int,
    tipo: EventoNotificacao | str,
    titulo: str,
    mensagem: str | None = None,
    vaga_id: int | None = None,
    pro: bool | None = None,
    commit: bool = True,
) -> Notificacao:
    """Grava uma notificação para `usuario_id` com `visivel_em` calculado pelo plano."""
    instante = agora()
    tipo_valor = tipo.value if isinstance(tipo, EventoNotificacao) else str(tipo)
    notificacao = Notificacao(
        usuario_id=usuario_id,
        tipo=tipo_valor,
        titulo=titulo[:120],
        mensagem=mensagem[:255] if mensagem is not None else None,
        vaga_id=vaga_id,
        visivel_em=calcular_visivel_em(db, usuario_id, pro=pro),
        criado_em=instante,
    )
    db.add(notificacao)
    if commit:
        db.commit()
        db.refresh(notificacao)
    return notificacao


def _usuarios_da_vaga(db: Session, vaga: Vaga) -> tuple[int | None, int | None]:
    """Devolve `(contratante_usuario_id, profissional_usuario_id)` da vaga."""
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
    return contratante_uid, profissional_uid


# ---------------------------------------------------------------------------
# Emissores dos 9 eventos de negócio (chamados pelos módulos)
# ---------------------------------------------------------------------------


def notificar_selecionados_top3(
    db: Session,
    vaga: Vaga,
    convites: list[Convite],
    *,
    commit: bool = True,
) -> list[Notificacao]:
    """Evento 1 (`selecionado_top3`): avisa cada profissional incluído na rodada do match."""
    criadas: list[Notificacao] = []
    for convite in convites:
        prof = db.get(PerfilProfissional, convite.profissional_id)
        if prof is None:
            continue
        criadas.append(
            criar_notificacao(
                db,
                usuario_id=prof.usuario_id,
                tipo=EventoNotificacao.SELECIONADO_TOP3,
                titulo=f"Você foi selecionado para a vaga: {vaga.titulo}",
                mensagem="Seu perfil ficou entre os 3 selecionados. Demonstre interesse na vaga.",
                vaga_id=vaga.id,
                commit=False,
            )
        )
    if commit and criadas:
        db.commit()
        for item in criadas:
            db.refresh(item)
    return criadas


def notificar_convite_escolhido(
    db: Session,
    vaga: Vaga,
    profissional_usuario_id: int,
    *,
    commit: bool = True,
) -> Notificacao:
    """Evento 2 (`convite_escolhido`): contratante escolheu o profissional para a vaga."""
    return criar_notificacao(
        db,
        usuario_id=profissional_usuario_id,
        tipo=EventoNotificacao.CONVITE_ESCOLHIDO,
        titulo=f"Você foi escolhido para a vaga: {vaga.titulo}",
        mensagem="O contratante escolheu você. Aceite o convite dentro do prazo.",
        vaga_id=vaga.id,
        commit=commit,
    )


def notificar_convite_aceito(
    db: Session,
    vaga: Vaga,
    *,
    contratante_usuario_id: int | None = None,
    commit: bool = True,
) -> Notificacao | None:
    """Evento 3 (`convite_aceito`): profissional aceitou o convite; avisa o contratante."""
    destinatario = contratante_usuario_id
    if destinatario is None:
        destinatario, _ = _usuarios_da_vaga(db, vaga)
    if destinatario is None:
        return None
    return criar_notificacao(
        db,
        usuario_id=destinatario,
        tipo=EventoNotificacao.CONVITE_ACEITO,
        titulo=f"Convite aceito na vaga: {vaga.titulo}",
        mensagem="O profissional aceitou o convite. Siga para o contrato e pagamento.",
        vaga_id=vaga.id,
        commit=commit,
    )


def notificar_contrato_pronto(
    db: Session,
    vaga: Vaga,
    *,
    commit: bool = True,
) -> list[Notificacao]:
    """Evento 4 (`contrato_pronto`): avisa contratante e profissional que o contrato está pronto."""
    contratante_uid, profissional_uid = _usuarios_da_vaga(db, vaga)
    criadas: list[Notificacao] = []
    for uid in (contratante_uid, profissional_uid):
        if uid is None:
            continue
        criadas.append(
            criar_notificacao(
                db,
                usuario_id=uid,
                tipo=EventoNotificacao.CONTRATO_PRONTO,
                titulo=f"Contrato pronto: {vaga.titulo}",
                mensagem="O resumo do acordo está disponível para aceite das condições.",
                vaga_id=vaga.id,
                commit=False,
            )
        )
    if commit and criadas:
        db.commit()
        for item in criadas:
            db.refresh(item)
    return criadas


def notificar_pagamento_retido(
    db: Session,
    vaga: Vaga,
    pagamento: Pagamento,
    *,
    commit: bool = True,
) -> Notificacao | None:
    """Evento 5 (`pagamento_retido`): Pix confirmado e retido; avisa o profissional contratado."""
    _, profissional_uid = _usuarios_da_vaga(db, vaga)
    if profissional_uid is None:
        return None
    return criar_notificacao(
        db,
        usuario_id=profissional_uid,
        tipo=EventoNotificacao.PAGAMENTO_RETIDO,
        titulo=f"Pagamento confirmado: {vaga.titulo}",
        mensagem=(
            f"O valor de R$ {pagamento.valor_total} foi retido com segurança até a conclusão."
        ),
        vaga_id=vaga.id,
        commit=commit,
    )


def notificar_lembrete_confirmacao(
    db: Session,
    vaga: Vaga,
    *,
    commit: bool = True,
) -> Notificacao | None:
    """Evento 6 (`lembrete_confirmacao`): lembra o contratante de confirmar a conclusão."""
    contratante_uid, _ = _usuarios_da_vaga(db, vaga)
    if contratante_uid is None:
        return None
    return criar_notificacao(
        db,
        usuario_id=contratante_uid,
        tipo=EventoNotificacao.LEMBRETE_CONFIRMACAO,
        titulo=f"Confirme a conclusão do serviço: {vaga.titulo}",
        mensagem="O horário previsto terminou. Confirme a conclusão e realize a validação facial.",
        vaga_id=vaga.id,
        commit=commit,
    )


def notificar_pagamento_liberado(
    db: Session,
    vaga: Vaga,
    *,
    parcial: bool = False,
    commit: bool = True,
) -> Notificacao | None:
    """Evento 7 (`pagamento_liberado`): valor liberado (integral ou parcial) ao profissional."""
    _, profissional_uid = _usuarios_da_vaga(db, vaga)
    if profissional_uid is None:
        return None
    modalidade = "parcialmente" if parcial else "integralmente"
    return criar_notificacao(
        db,
        usuario_id=profissional_uid,
        tipo=EventoNotificacao.PAGAMENTO_LIBERADO,
        titulo=f"Pagamento liberado: {vaga.titulo}",
        mensagem=f"O pagamento da vaga foi liberado {modalidade} para você.",
        vaga_id=vaga.id,
        commit=commit,
    )


def notificar_avaliacao_pendente(
    db: Session,
    vaga: Vaga,
    *,
    commit: bool = True,
) -> list[Notificacao]:
    """Evento 8 (`avaliacao_pendente`): convida contratante e profissional a avaliarem em 48h."""
    contratante_uid, profissional_uid = _usuarios_da_vaga(db, vaga)
    criadas: list[Notificacao] = []
    for uid in (contratante_uid, profissional_uid):
        if uid is None:
            continue
        criadas.append(
            criar_notificacao(
                db,
                usuario_id=uid,
                tipo=EventoNotificacao.AVALIACAO_PENDENTE,
                titulo=f"Avalie o serviço concluído: {vaga.titulo}",
                mensagem="Você tem 48 horas para avaliar a outra parte de 1 a 5 estrelas.",
                vaga_id=vaga.id,
                commit=False,
            )
        )
    if commit and criadas:
        db.commit()
        for item in criadas:
            db.refresh(item)
    return criadas


def notificar_denuncia_decidida(
    db: Session,
    denuncia: Denuncia,
    *,
    penalidades: int = 0,
    limite: int = 0,
    banido: bool = False,
    commit: bool = True,
) -> list[Notificacao]:
    """Evento 9 (`denuncia_decidida`): informa o resultado da análise da denúncia às partes."""
    procedente = denuncia.estado == EstadoDenuncia.PROCEDENTE
    if procedente:
        para_denunciado = (
            "Denúncia procedente: a conta foi banida de forma permanente."
            if banido
            else (
                f"Denúncia procedente: o chat com a outra parte foi bloqueado. "
                f"Penalidade {penalidades} de {limite}; ao atingir {limite} a conta é banida."
            )
        )
        para_denunciante = "Denúncia procedente: o chat com a outra parte foi bloqueado."
    else:
        para_denunciado = "Decisão registrada: denúncia improcedente."
        para_denunciante = "Decisão registrada: denúncia improcedente."

    destinatarios: list[tuple[int, str]] = [(denuncia.denunciado_id, para_denunciado)]
    if denuncia.denunciante_id is not None and denuncia.denunciante_id != denuncia.denunciado_id:
        destinatarios.append((denuncia.denunciante_id, para_denunciante))

    criadas: list[Notificacao] = []
    for uid, texto in destinatarios:
        criadas.append(
            criar_notificacao(
                db,
                usuario_id=uid,
                tipo=EventoNotificacao.DENUNCIA_DECIDIDA,
                titulo="Denúncia analisada pela administração",
                mensagem=texto,
                vaga_id=denuncia.vaga_id,
                commit=False,
            )
        )
    if commit and criadas:
        db.commit()
        for item in criadas:
            db.refresh(item)
    return criadas


@varredura("lembretes_confirmacao")
def varrer_lembretes_de_confirmacao(db: Session) -> int:
    """Envia lembrete de confirmação para vagas contratadas/em execução cujo fim já passou."""
    instante = agora()
    vagas = db.scalars(
        select(Vaga).where(
            Vaga.estado.in_(
                (EstadoVaga.CONTRATADA, EstadoVaga.EM_EXECUCAO, EstadoVaga.ENCERRADA)
            ),
            Vaga.inicio_em <= instante,
        )
    ).all()

    enviados = 0
    for vaga in vagas:
        fim = como_utc(vaga.inicio_em) + timedelta(minutes=vaga.duracao_minutos)
        if fim > instante:
            continue
        ja_enviado = db.scalar(
            select(Notificacao.id).where(
                Notificacao.vaga_id == vaga.id,
                Notificacao.tipo == EventoNotificacao.LEMBRETE_CONFIRMACAO.value,
            )
        )
        if ja_enviado is not None:
            continue
        if notificar_lembrete_confirmacao(db, vaga, commit=False) is not None:
            enviados += 1
    if enviados:
        db.commit()
    return enviados


# ---------------------------------------------------------------------------
# Consulta por polling REST e marcação de leitura
# ---------------------------------------------------------------------------


def montar_detalhe(notificacao: Notificacao) -> NotificacaoDetalhe:
    return NotificacaoDetalhe(
        id=notificacao.id,
        usuario_id=notificacao.usuario_id,
        tipo=notificacao.tipo,
        titulo=notificacao.titulo,
        mensagem=notificacao.mensagem,
        vaga_id=notificacao.vaga_id,
        visivel_em=_em_utc(notificacao.visivel_em),
        lida_em=_em_utc(notificacao.lida_em),
        lida=notificacao.lida_em is not None,
        criado_em=_em_utc(notificacao.criado_em),
    )


def contar_nao_lidas(db: Session, usuario_id: int) -> int:
    """Conta notificações do usuário já visíveis (`visivel_em <= agora()`) e ainda não lidas."""
    instante = agora()
    total = db.scalar(
        select(func.count(Notificacao.id)).where(
            Notificacao.usuario_id == usuario_id,
            Notificacao.visivel_em <= instante,
            Notificacao.lida_em.is_(None),
        )
    )
    return int(total or 0)


def listar_notificacoes_visiveis(
    db: Session,
    usuario_id: int,
    *,
    apenas_nao_lidas: bool = False,
) -> CentralNotificacoes:
    """Retorna as notificações cujo `visivel_em <= agora()` e o total de não lidas visíveis."""
    instante = agora()
    consulta = select(Notificacao).where(
        Notificacao.usuario_id == usuario_id,
        Notificacao.visivel_em <= instante,
    )
    if apenas_nao_lidas:
        consulta = consulta.where(Notificacao.lida_em.is_(None))
    itens = db.scalars(consulta.order_by(Notificacao.id.desc())).all()
    return CentralNotificacoes(
        nao_lidas=contar_nao_lidas(db, usuario_id),
        itens=[montar_detalhe(item) for item in itens],
    )


def marcar_como_lida(
    db: Session,
    notificacao_id: int,
    usuario: Usuario,
) -> NotificacaoDetalhe:
    """Marca uma notificação visível do usuário como lida (`lida_em = agora()`)."""
    instante = agora()
    notificacao = db.get(Notificacao, notificacao_id)
    if notificacao is None or como_utc(notificacao.visivel_em) > instante:
        raise erro_http(
            status.HTTP_404_NOT_FOUND,
            "notificacao_nao_encontrada",
            "Notificação não encontrada.",
        )
    if notificacao.usuario_id != usuario.id:
        raise erro_http(
            status.HTTP_403_FORBIDDEN,
            "notificacao_de_outro_usuario",
            "Você só pode marcar suas próprias notificações como lidas.",
        )
    if notificacao.lida_em is None:
        notificacao.lida_em = instante
        db.commit()
        db.refresh(notificacao)
    return montar_detalhe(notificacao)


def marcar_todas_como_lidas(db: Session, usuario: Usuario) -> ResultadoLeituraTodas:
    """Marca como lidas todas as notificações já visíveis e ainda não lidas do usuário."""
    instante = agora()
    resultado = db.execute(
        update(Notificacao)
        .where(
            Notificacao.usuario_id == usuario.id,
            Notificacao.visivel_em <= instante,
            Notificacao.lida_em.is_(None),
        )
        .values(lida_em=instante)
    )
    db.commit()
    marcadas = int(resultado.rowcount or 0)
    return ResultadoLeituraTodas(
        marcadas=marcadas,
        lida_em=_em_utc(instante) if marcadas > 0 else None,
    )
