"""Denúncias (chat e perfil), fila do admin com meta de 48h úteis e decisão (US57 / SEG05).

Regras:
- Denúncia pode ser registrada a partir de um perfil (`denunciado_id`), de uma vaga (`vaga_id`)
  ou de uma conversa de chat (`conversa_id`), com `motivo` e `descricao`.
- Denúncias automáticas do chat (US46, `origem = automatica`) entram na mesma fila administrativa.
- A fila do admin exibe o tempo em espera (`tempo_espera_minutos` e `tempo_espera_horas_uteis`,
  com `meta_horas_uteis = 48` e indicador `acima_da_meta`).
- Decisão do admin (`procedente` ou `improcedente`) exige motivo, grava `decidido_em` e:
  - Quando `procedente`, a denúncia é uma penalidade ao denunciado: bloqueia o chat entre as
    duas partes envolvidas (somente ele; vagas, match e demais funções seguem livres) e, ao
    atingir `denuncias_para_banimento` penalidades, a conta é banida de forma permanente.
- Privacidade: a visão de usuário (`DenunciaPublicaResumo`) jamais expõe `denunciante_id`, de
  modo que o denunciado nunca vê quem o denunciou.
"""

from datetime import datetime, timedelta

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.schemas.denuncias import (
    DenunciaEntrada,
    DenunciaFilaAdmin,
    DenunciaPublicaResumo,
    UsuarioBanido,
)
from app.erros import erro_http
from app.models import (
    Conversa,
    Denuncia,
    EstadoConta,
    EstadoDenuncia,
    OrigemDenuncia,
    PerfilContratante,
    PerfilProfissional,
    Usuario,
    Vaga,
)
from app.services import configuracao, notificacoes
from app.tempo import agora, como_utc

META_FILA_HORAS_UTEIS = 48


def _em_utc(instante: datetime | None) -> datetime | None:
    return como_utc(instante) if instante is not None else None


def _participantes_da_vaga(db: Session, vaga: Vaga) -> tuple[int | None, int | None]:
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
        if cursor.weekday() < 5:  # 0 = segunda ... 4 = sexta
            segundos_uteis += (limite - cursor).total_seconds()
        cursor = limite

    return round(segundos_uteis / 3600.0, 2)


def _resolver_referencias(
    db: Session,
    denunciante: Usuario,
    entrada: DenunciaEntrada,
) -> tuple[int, int | None, int | None]:
    """Valida e resolve `(denunciado_id, vaga_id, conversa_id)` a partir da entrada."""
    vaga: Vaga | None = None
    conversa: Conversa | None = None

    if entrada.conversa_id is not None:
        conversa = db.get(Conversa, entrada.conversa_id)
        if conversa is None:
            raise erro_http(
                status.HTTP_404_NOT_FOUND,
                "conversa_nao_encontrada",
                "Conversa não encontrada.",
            )
        vaga = db.get(Vaga, conversa.vaga_id)

    if entrada.vaga_id is not None:
        vaga_informada = db.get(Vaga, entrada.vaga_id)
        if vaga_informada is None:
            raise erro_http(
                status.HTTP_404_NOT_FOUND,
                "vaga_nao_encontrada",
                "Vaga não encontrada.",
            )
        if conversa is not None and conversa.vaga_id != vaga_informada.id:
            raise erro_http(
                status.HTTP_409_CONFLICT,
                "conversa_de_outra_vaga",
                "A conversa informada não pertence à vaga indicada.",
            )
        vaga = vaga_informada

    denunciado_id = entrada.denunciado_id

    if vaga is not None:
        contratante_uid, profissional_uid = _participantes_da_vaga(db, vaga)
        if denunciante.id not in (contratante_uid, profissional_uid):
            raise erro_http(
                status.HTTP_403_FORBIDDEN,
                "nao_participa_da_vaga",
                "Somente participantes da vaga ou conversa podem abrir denúncia vinculada a ela.",
            )
        outra_parte = profissional_uid if denunciante.id == contratante_uid else contratante_uid
        if denunciado_id is None:
            if outra_parte is None:
                raise erro_http(
                    status.HTTP_409_CONFLICT,
                    "vaga_sem_outra_parte",
                    "A vaga ainda não possui outra parte para denunciar.",
                )
            denunciado_id = outra_parte
        elif outra_parte is not None and denunciado_id != outra_parte:
            raise erro_http(
                status.HTTP_409_CONFLICT,
                "denunciado_fora_da_vaga",
                "O usuário denunciado não corresponde à outra parte desta vaga ou conversa.",
            )

    if denunciado_id is None:
        raise erro_http(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "denunciado_obrigatorio",
            "Informe o usuário denunciado, a vaga ou a conversa.",
        )

    if denunciado_id == denunciante.id:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "autodenuncia",
            "Não é permitido denunciar a própria conta.",
        )

    if db.get(Usuario, denunciado_id) is None:
        raise erro_http(
            status.HTTP_404_NOT_FOUND,
            "usuario_nao_encontrado",
            "Usuário denunciado não encontrado.",
        )

    vaga_id = vaga.id if vaga is not None else None
    conversa_id = conversa.id if conversa is not None else None
    return denunciado_id, vaga_id, conversa_id


def criar_denuncia(
    db: Session,
    denunciante: Usuario,
    entrada: DenunciaEntrada,
) -> Denuncia:
    """Cria uma denúncia manual de usuário (a partir do perfil, vaga ou chat) na fila do admin."""
    denunciado_id, vaga_id, conversa_id = _resolver_referencias(db, denunciante, entrada)
    denuncia = Denuncia(
        denunciante_id=denunciante.id,
        denunciado_id=denunciado_id,
        vaga_id=vaga_id,
        conversa_id=conversa_id,
        origem=OrigemDenuncia.USUARIO,
        motivo=entrada.motivo,
        descricao=entrada.descricao,
        estado=EstadoDenuncia.ABERTA,
    )
    db.add(denuncia)
    db.commit()
    db.refresh(denuncia)
    return denuncia


def montar_resumo_publico(denuncia: Denuncia) -> DenunciaPublicaResumo:
    """Monta a representação pública da denúncia sem `denunciante_id` (protege o denunciante)."""
    return DenunciaPublicaResumo(
        id=denuncia.id,
        denunciado_id=denuncia.denunciado_id,
        vaga_id=denuncia.vaga_id,
        conversa_id=denuncia.conversa_id,
        motivo=denuncia.motivo,
        descricao=denuncia.descricao,
        estado=denuncia.estado,
        decisao_motivo=denuncia.decisao_motivo,
        criado_em=_em_utc(denuncia.criado_em),
        decidido_em=_em_utc(denuncia.decidido_em),
    )


def listar_denuncias_do_usuario(db: Session, usuario: Usuario) -> list[DenunciaPublicaResumo]:
    """Lista as denúncias relacionadas ao usuário (enviadas ou recebidas) sem `denunciante_id`."""
    itens = db.scalars(
        select(Denuncia)
        .where((Denuncia.denunciante_id == usuario.id) | (Denuncia.denunciado_id == usuario.id))
        .order_by(Denuncia.id.desc())
    ).all()
    return [montar_resumo_publico(item) for item in itens]


def consultar_denuncia_publica(
    db: Session,
    denuncia_id: int,
    usuario: Usuario,
) -> DenunciaPublicaResumo:
    """Consulta uma denúncia pelo participante (autor ou denunciado) sem expor `denunciante_id`."""
    denuncia = db.get(Denuncia, denuncia_id)
    if denuncia is None:
        raise erro_http(
            status.HTTP_404_NOT_FOUND,
            "denuncia_nao_encontrada",
            "Denúncia não encontrada.",
        )
    if usuario.id not in (denuncia.denunciante_id, denuncia.denunciado_id):
        raise erro_http(
            status.HTTP_403_FORBIDDEN,
            "sem_acesso_a_denuncia",
            "Você não tem permissão para consultar esta denúncia.",
        )
    return montar_resumo_publico(denuncia)


def total_procedentes_do_usuario(db: Session, usuario_id: int) -> int:
    return int(
        db.scalar(
            select(func.count(Denuncia.id)).where(
                Denuncia.denunciado_id == usuario_id,
                Denuncia.estado == EstadoDenuncia.PROCEDENTE,
            )
        )
        or 0
    )


def montar_item_fila_admin(
    db: Session,
    denuncia: Denuncia,
    *,
    procedentes_cache: dict[int, int] | None = None,
) -> DenunciaFilaAdmin:
    """Monta o item da fila administrativa com cálculo de espera e meta de 48h úteis."""
    criado = como_utc(denuncia.criado_em)
    fim_espera = como_utc(denuncia.decidido_em) if denuncia.decidido_em is not None else agora()
    espera_segundos = max(0.0, (fim_espera - criado).total_seconds())
    espera_minutos = int(espera_segundos // 60)
    horas_uteis = calcular_horas_uteis(criado, fim_espera)

    if procedentes_cache is not None and denuncia.denunciado_id in procedentes_cache:
        procedentes = procedentes_cache[denuncia.denunciado_id]
    else:
        procedentes = total_procedentes_do_usuario(db, denuncia.denunciado_id)
        if procedentes_cache is not None:
            procedentes_cache[denuncia.denunciado_id] = procedentes

    denunciado = db.get(Usuario, denuncia.denunciado_id)
    return DenunciaFilaAdmin(
        id=denuncia.id,
        denunciante_id=denuncia.denunciante_id,
        denunciado_id=denuncia.denunciado_id,
        vaga_id=denuncia.vaga_id,
        conversa_id=denuncia.conversa_id,
        origem=denuncia.origem,
        motivo=denuncia.motivo,
        descricao=denuncia.descricao,
        estado=denuncia.estado,
        decisao_motivo=denuncia.decisao_motivo,
        criado_em=criado,
        decidido_em=_em_utc(denuncia.decidido_em),
        tempo_espera_minutos=espera_minutos,
        tempo_espera_horas_uteis=horas_uteis,
        meta_horas_uteis=META_FILA_HORAS_UTEIS,
        acima_da_meta=horas_uteis > META_FILA_HORAS_UTEIS,
        denuncias_procedentes_acumuladas=procedentes,
        denunciado_estado=denunciado.estado if denunciado is not None else None,
        conversa_bloqueada=denuncia.estado == EstadoDenuncia.PROCEDENTE
        and (denuncia.conversa_id is not None or denuncia.vaga_id is not None),
    )


def listar_fila_admin(
    db: Session,
    *,
    estado: EstadoDenuncia | None = None,
    origem: OrigemDenuncia | None = None,
) -> list[DenunciaFilaAdmin]:
    """Lista a fila administrativa de denúncias (abertas primeiro, da mais antiga em espera)."""
    consulta = select(Denuncia)
    if estado is not None:
        consulta = consulta.where(Denuncia.estado == estado)
    if origem is not None:
        consulta = consulta.where(Denuncia.origem == origem)
    # Denúncias abertas vêm em ordem de chegada (FIFO, maior tempo em espera primeiro)
    itens = db.scalars(consulta.order_by(Denuncia.criado_em.asc(), Denuncia.id.asc())).all()
    cache: dict[int, int] = {}
    return [montar_item_fila_admin(db, item, procedentes_cache=cache) for item in itens]


def _banir_se_atingiu_limite(db: Session, denunciado_id: int) -> tuple[int, int, bool]:
    """Conta as penalidades do denunciado e o bane de forma permanente ao atingir o limite.

    Devolve `(penalidades, limite, banido)`.
    """
    penalidades = total_procedentes_do_usuario(db, denunciado_id)
    limite = int(configuracao.get("denuncias_para_banimento", db=db))
    banido = False
    if penalidades >= limite:
        usuario = db.get(Usuario, denunciado_id)
        if usuario is not None:
            usuario.estado = EstadoConta.BANIDA
            usuario.suspensa_ate = None
            banido = True
    return penalidades, limite, banido


def decidir_denuncia(
    db: Session,
    denuncia_id: int,
    *,
    decisao: str,
    motivo: str,
) -> DenunciaFilaAdmin:
    """Registra a decisão administrativa (`procedente` ou `improcedente`) com motivo."""
    denuncia = db.get(Denuncia, denuncia_id)
    if denuncia is None:
        raise erro_http(
            status.HTTP_404_NOT_FOUND,
            "denuncia_nao_encontrada",
            "Denúncia não encontrada.",
        )
    if denuncia.estado != EstadoDenuncia.ABERTA:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "denuncia_ja_decidida",
            "Esta denúncia já foi decidida.",
            estado_atual=denuncia.estado.value,
        )

    novo_estado = (
        EstadoDenuncia.PROCEDENTE
        if decisao == EstadoDenuncia.PROCEDENTE.value
        else EstadoDenuncia.IMPROCEDENTE
    )
    denuncia.estado = novo_estado
    denuncia.decisao_motivo = motivo.strip()
    denuncia.decidido_em = agora()
    db.flush()

    penalidades, limite, banido = 0, int(configuracao.get("denuncias_para_banimento", db=db)), False
    if novo_estado == EstadoDenuncia.PROCEDENTE:
        penalidades, limite, banido = _banir_se_atingiu_limite(db, denuncia.denunciado_id)

    notificacoes.notificar_denuncia_decidida(
        db, denuncia, penalidades=penalidades, limite=limite, banido=banido, commit=False
    )
    db.commit()
    db.refresh(denuncia)
    return montar_item_fila_admin(db, denuncia)


def listar_banidos(db: Session) -> list[UsuarioBanido]:
    """Contas banidas, com a quantidade de penalidades que levou ao banimento."""
    usuarios = db.scalars(
        select(Usuario).where(Usuario.estado == EstadoConta.BANIDA, Usuario.excluida_em.is_(None))
    ).all()
    return [
        UsuarioBanido(
            usuario_id=u.id,
            nome=u.nome,
            email=u.email,
            penalidades=total_procedentes_do_usuario(db, u.id),
        )
        for u in usuarios
    ]
