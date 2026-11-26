"""Rotas de vagas, convites e top 3: publicação, oportunidades, escolha e aceite."""

from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.schemas.vagas import (
    AceiteResposta,
    CategoriaResumo,
    ContratanteResumo,
    ConviteComVaga,
    ConviteContratante,
    ConviteVaga,
    EscolhaEntrada,
    InteresseEntrada,
    ProfissionalRecomendado,
    Top3Contratacao,
    VagaCriar,
    VagaDetalhe,
)
from app.db import get_db
from app.deps import exige_conta_ativa, exige_perfil, usuario_atual
from app.erros import erro_http
from app.models import (
    Avaliacao,
    Categoria,
    Conversa,
    Convite,
    Endereco,
    EstadoConvite,
    EstadoVaga,
    Notificacao,
    PerfilContratante,
    PerfilProfissional,
    RiscoCategoria,
    TipoPerfil,
    Usuario,
    Vaga,
)
from app.services import bairros, comprovantes, configuracao, match
from app.services import vagas as vagas_servico
from app.services.geo import arredondar_km, distancia_km
from app.tempo import agora, como_utc

router = APIRouter(tags=["vagas"])

# Centro usado quando o profissional ainda não tem endereço cadastrado (Marco Zero, Recife).
CENTRO_PADRAO = (-8.0631, -34.8711)
FUSO_RECIFE = timedelta(hours=-3)

STATUS_VAGA = {
    EstadoVaga.PUBLICADA: "publicada",
    EstadoVaga.AGUARDANDO_ACEITE: "em_contratacao",
    EstadoVaga.ACEITA: "aceita",
    EstadoVaga.AGUARDANDO_PAGAMENTO: "aguardando_pagamento",
    EstadoVaga.CONTRATADA: "contratada",
    EstadoVaga.EM_EXECUCAO: "em_execucao",
    EstadoVaga.ENCERRADA: "aguardando_confirmacao",
    EstadoVaga.EM_DISPUTA: "em_disputa",
    EstadoVaga.CONCLUIDA: "concluida",
    EstadoVaga.LIBERADA: "concluida",
    EstadoVaga.LIBERADA_PARCIAL: "concluida",
    EstadoVaga.REEMBOLSADA: "concluida",
    EstadoVaga.CANCELADA: "cancelada",
    EstadoVaga.FALTA: "cancelada",
    EstadoVaga.EXPIRADA: "expirada",
}

STATUS_CONVITE = {
    EstadoConvite.SUGERIDO: "pendente_interesse",
    EstadoConvite.INTERESSADO: "interessado",
    EstadoConvite.ESCOLHIDO: "escolhido",
    EstadoConvite.ACEITO: "aceito",
    EstadoConvite.RECUSADO: "recusado",
    EstadoConvite.EXPIRADO: "expirado",
}

NIVEL_RISCO = {
    RiscoCategoria.BAIXO: "baixo",
    RiscoCategoria.MEDIO: "medio",
    RiscoCategoria.ALTO: "alto",
}


def contratante_ativo(
    usuario: Usuario = Depends(exige_perfil(TipoPerfil.CONTRATANTE)),
    _: Usuario = Depends(exige_conta_ativa),
) -> Usuario:
    return usuario


def profissional_ativo(
    usuario: Usuario = Depends(exige_perfil(TipoPerfil.PROFISSIONAL)),
    _: Usuario = Depends(exige_conta_ativa),
) -> Usuario:
    return usuario


# ---------------------------------------------------------------- conversões


def reputacao_do_usuario(db: Session, usuario_id: int) -> tuple[float, int]:
    media, total = db.execute(
        select(func.avg(Avaliacao.nota), func.count(Avaliacao.id)).where(
            Avaliacao.avaliado_id == usuario_id, Avaliacao.visivel_em.is_not(None)
        )
    ).one()
    return (round(float(media), 1) if media is not None else match.NOTA_SEM_AVALIACAO, total)


def _resumo_contratante(db: Session, contratante: PerfilContratante) -> ContratanteResumo:
    usuario = db.get(Usuario, contratante.usuario_id)
    nota, total = reputacao_do_usuario(db, contratante.usuario_id)
    destaque = comprovantes.destaque_do_contratante(db, contratante.usuario_id)
    return ContratanteResumo(
        id=str(contratante.id),
        usuario_id=contratante.usuario_id,
        nome=contratante.razao_social or usuario.nome,
        foto_url=contratante.avatar_url,
        verificado=contratante.verificado,
        nota_media=nota,
        total_avaliacoes=total,
        adicionais_pagos=destaque.adicionais_pagos,
        destaque=destaque.destaque,
        cor_destaque=contratante.cor_destaque,
    )


def _convite_para_front(convite: Convite) -> ConviteVaga:
    return ConviteVaga(
        id=str(convite.id),
        vaga_id=str(convite.vaga_id),
        profissional_id=str(convite.profissional_id),
        status=STATUS_CONVITE[convite.estado],
        valor_proposto=float(convite.valor_proposto) if convite.valor_proposto else None,
        prazo_em=como_utc(convite.expira_em) if convite.expira_em else None,
        rodada=convite.rodada,
    )


def _vaga_para_front(
    db: Session, vaga: Vaga, usuario: Usuario, origem: tuple[float, float] | None = None
) -> VagaDetalhe:
    endereco = db.get(Endereco, vaga.endereco_id)
    categoria = db.get(Categoria, vaga.categoria_id)
    contratante = db.get(PerfilContratante, vaga.contratante_id)
    profissional = usuario.perfil_profissional

    if origem is None and profissional is not None and profissional.endereco is not None:
        origem = (profissional.endereco.latitude, profissional.endereco.longitude)
    distancia = (
        arredondar_km(distancia_km(origem[0], origem[1], endereco.latitude, endereco.longitude))
        if origem
        else 0.0
    )

    convite = None
    if profissional is not None:
        convite = db.scalars(
            select(Convite)
            .where(Convite.vaga_id == vaga.id, Convite.profissional_id == profissional.id)
            .order_by(Convite.rodada.desc())
        ).first()

    parte = (
        usuario.perfil_contratante is not None and contratante.id == usuario.perfil_contratante.id
    ) or (profissional is not None and vaga.profissional_id == profissional.id)
    completo = None
    if parte:
        partes = [endereco.logradouro, endereco.numero, endereco.bairro, endereco.cidade]
        completo = ", ".join(p for p in partes if p)

    return VagaDetalhe(
        id=str(vaga.id),
        titulo=vaga.titulo,
        descricao=vaga.descricao,
        categoria=categoria.nome,
        nivel_risco=NIVEL_RISCO[categoria.risco],
        valor=float(vaga.valor_fixado or vaga.valor_inicial),
        duracao_horas=round(vaga.duracao_minutos / 60, 2),
        data_inicio=como_utc(vaga.inicio_em),
        bairro=endereco.bairro,
        distancia_km=distancia,
        # Quem não é parte da vaga vê o pino aproximado (2 casas decimais, cerca de 1 km).
        latitude=endereco.latitude if parte else round(endereco.latitude, 2),
        longitude=endereco.longitude if parte else round(endereco.longitude, 2),
        endereco_completo=completo,
        status=STATUS_VAGA[vaga.estado],
        contratante=_resumo_contratante(db, contratante),
        meu_convite=_convite_para_front(convite) if convite else None,
        checkin_realizado_em=como_utc(vaga.checkin_em) if vaga.checkin_em else None,
        falta_registrada=vaga.estado == EstadoVaga.FALTA,
    )


def _notificar(db: Session, usuario_id: int, tipo: str, titulo: str, vaga: Vaga) -> None:
    db.add(Notificacao(usuario_id=usuario_id, tipo=tipo, titulo=titulo, vaga_id=vaga.id))


def _vaga_obrigatoria(db: Session, vaga_id: int) -> Vaga:
    vaga = db.get(Vaga, vaga_id)
    if vaga is None:
        raise erro_http(status.HTTP_404_NOT_FOUND, "vaga_nao_encontrada", "Vaga não encontrada.")
    return vaga


# ---------------------------------------------------------------- categorias e vagas


@router.get("/categorias", response_model=list[CategoriaResumo])
def listar_categorias(
    _: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)
) -> list[CategoriaResumo]:
    """Categorias de serviço disponíveis para publicar uma vaga."""
    categorias = db.scalars(select(Categoria).order_by(Categoria.nome)).all()
    return [
        CategoriaResumo(slug=c.slug, nome=c.nome, nivel_risco=NIVEL_RISCO[c.risco])
        for c in categorias
    ]


def _exigir_checklist_de_seguranca(categoria: Categoria, respostas) -> None:
    """Categorias de risco médio ou alto exigem o checklist; item crítico negativo bloqueia."""
    if categoria.risco == RiscoCategoria.BAIXO:
        return
    criticos = [item for item in categoria.checklist if item.critico]
    por_item = {r.item_id: r.atendido for r in respostas}
    sem_resposta = [item.id for item in criticos if item.id not in por_item]
    if sem_resposta:
        raise erro_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "checklist_incompleto",
            "Responda todos os itens críticos do checklist de segurança.",
            itens_pendentes=sem_resposta,
        )
    negativos = [item.id for item in criticos if not por_item[item.id]]
    if negativos:
        raise erro_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "checklist_critico_negativo",
            "Há item crítico de segurança não atendido; a vaga não pode ser publicada.",
            itens_nao_atendidos=negativos,
        )


@router.post("/vagas", response_model=VagaDetalhe, status_code=status.HTTP_201_CREATED)
def criar_vaga(
    entrada: VagaCriar,
    usuario: Usuario = Depends(contratante_ativo),
    db: Session = Depends(get_db),
) -> VagaDetalhe:
    """Publica uma vaga e já gera a primeira rodada do top 3."""
    categoria = db.scalars(select(Categoria).where(Categoria.slug == entrada.categoria)).first()
    if categoria is None:
        raise erro_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "categoria_invalida", "Categoria inexistente."
        )
    _exigir_checklist_de_seguranca(categoria, entrada.checklist)
    inicio = como_utc(entrada.data_inicio)
    antecedencia = configuracao.get("inicio_minimo_vaga_min", db=db)
    if inicio < agora() + timedelta(minutes=antecedencia):
        raise erro_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "inicio_muito_proximo",
            "O início precisa respeitar a antecedência mínima.",
            antecedencia_minima_min=antecedencia,
        )
    latitude, longitude = bairros.resolver_coordenadas(
        entrada.bairro, entrada.latitude, entrada.longitude
    )
    endereco = Endereco(
        logradouro=entrada.logradouro,
        bairro=entrada.bairro,
        latitude=latitude,
        longitude=longitude,
    )
    db.add(endereco)
    db.flush()
    vaga = Vaga(
        contratante_id=usuario.perfil_contratante.id,
        categoria_id=categoria.id,
        endereco_id=endereco.id,
        titulo=entrada.titulo,
        descricao=entrada.descricao,
        inicio_em=inicio,
        duracao_minutos=round(entrada.duracao_horas * 60),
        valor_inicial=Decimal(str(entrada.valor)).quantize(Decimal("0.01")),
        estado=EstadoVaga.PUBLICADA,
        prazo_em=vagas_servico.calcular_prazo_publicacao(db, inicio),
    )
    db.add(vaga)
    db.commit()
    db.refresh(vaga)
    match.gerar_rodada(db, vaga)
    return _vaga_para_front(db, vaga, usuario)


@router.get("/vagas/oportunidades", response_model=list[VagaDetalhe])
def listar_oportunidades(
    raio_km: int = Query(default=5, ge=1, le=15),
    categoria: str | None = None,
    data: str | None = Query(default=None, pattern="^(hoje|amanha)$"),
    usuario: Usuario = Depends(profissional_ativo),
    db: Session = Depends(get_db),
) -> list[VagaDetalhe]:
    """Vagas publicadas dentro do raio do profissional, da mais próxima para a mais distante."""
    perfil = usuario.perfil_profissional
    origem = (
        (perfil.endereco.latitude, perfil.endereco.longitude) if perfil.endereco else CENTRO_PADRAO
    )
    consulta = (
        select(Vaga)
        .join(Endereco, Endereco.id == Vaga.endereco_id)
        .join(Categoria, Categoria.id == Vaga.categoria_id)
        .where(Vaga.estado == EstadoVaga.PUBLICADA, Vaga.inicio_em > agora())
    )
    if categoria:
        consulta = consulta.where((Categoria.slug == categoria) | (Categoria.nome == categoria))
    resultado = []
    hoje = (agora() + FUSO_RECIFE).date()
    for vaga in db.scalars(consulta):
        endereco = db.get(Endereco, vaga.endereco_id)
        distancia = distancia_km(origem[0], origem[1], endereco.latitude, endereco.longitude)
        if distancia > raio_km:
            continue
        dia = (como_utc(vaga.inicio_em) + FUSO_RECIFE).date()
        if data == "hoje" and dia != hoje:
            continue
        if data == "amanha" and dia != hoje + timedelta(days=1):
            continue
        resultado.append(_vaga_para_front(db, vaga, usuario, origem))
    return sorted(resultado, key=lambda v: v.distancia_km)


@router.get("/vagas/{vaga_id}", response_model=VagaDetalhe)
def detalhar_vaga(
    vaga_id: int, usuario: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)
) -> VagaDetalhe:
    """Detalhe de uma vaga. O endereço completo só aparece para as partes da contratação."""
    return _vaga_para_front(db, _vaga_obrigatoria(db, vaga_id), usuario)


@router.get("/contratante/vagas", response_model=list[VagaDetalhe])
def listar_minhas_vagas(
    usuario: Usuario = Depends(contratante_ativo), db: Session = Depends(get_db)
) -> list[VagaDetalhe]:
    """Vagas publicadas pelo contratante, da mais recente para a mais antiga."""
    vagas = db.scalars(
        select(Vaga)
        .where(Vaga.contratante_id == usuario.perfil_contratante.id)
        .order_by(Vaga.id.desc())
    )
    return [_vaga_para_front(db, v, usuario) for v in vagas]


# ---------------------------------------------------------------- profissional


def _convite_do_profissional(db: Session, convite_id: int, perfil: PerfilProfissional) -> Convite:
    convite = db.get(Convite, convite_id)
    if convite is None or convite.profissional_id != perfil.id:
        raise erro_http(
            status.HTTP_404_NOT_FOUND, "convite_nao_encontrado", "Convite não encontrado."
        )
    return convite


@router.post("/vagas/{vaga_id}/interesse", response_model=ConviteVaga)
def manifestar_interesse(
    vaga_id: int,
    entrada: InteresseEntrada,
    usuario: Usuario = Depends(profissional_ativo),
    db: Session = Depends(get_db),
) -> ConviteVaga:
    """Marca o convite do profissional como interessado, com contraproposta opcional."""
    vaga = _vaga_obrigatoria(db, vaga_id)
    perfil = usuario.perfil_profissional
    convite = db.scalars(
        select(Convite)
        .where(Convite.vaga_id == vaga.id, Convite.profissional_id == perfil.id)
        .order_by(Convite.rodada.desc())
    ).first()
    if convite is None:
        raise erro_http(
            status.HTTP_403_FORBIDDEN,
            "fora_do_top3",
            "Esta vaga não está entre as sugeridas para o seu perfil.",
        )
    if convite.estado == EstadoConvite.INTERESSADO:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "interesse_ja_registrado",
            "Você já demonstrou interesse nesta vaga; só é permitida uma contraproposta.",
        )
    if convite.estado != EstadoConvite.SUGERIDO:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "interesse_indisponivel",
            "Você não tem um convite aguardando interesse nesta vaga.",
        )
    if entrada.valor_proposto is not None:
        teto = float(vaga.valor_inicial) * (
            1 + configuracao.get("contraproposta_max_pct", db=db) / 100
        )
        if entrada.valor_proposto > teto:
            raise erro_http(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "contraproposta_acima_do_limite",
                "A contraproposta excede o acréscimo máximo permitido.",
                valor_maximo=round(teto, 2),
            )
        convite.valor_proposto = Decimal(str(entrada.valor_proposto)).quantize(Decimal("0.01"))
    convite.estado = EstadoConvite.INTERESSADO
    db.commit()
    db.refresh(convite)
    return _convite_para_front(convite)


@router.get("/profissional/convites", response_model=list[ConviteComVaga])
def listar_convites_do_profissional(
    usuario: Usuario = Depends(profissional_ativo), db: Session = Depends(get_db)
) -> list[ConviteComVaga]:
    """Convites em que o contratante já escolheu o profissional e o prazo de aceite segue aberto."""
    perfil = usuario.perfil_profissional
    convites = db.scalars(
        select(Convite)
        .where(
            Convite.profissional_id == perfil.id,
            Convite.estado == EstadoConvite.ESCOLHIDO,
        )
        .order_by(Convite.id.desc())
    )
    itens = []
    for convite in convites:
        vaga = db.get(Vaga, convite.vaga_id)
        if vaga.estado != EstadoVaga.AGUARDANDO_ACEITE or (
            convite.expira_em is not None and como_utc(convite.expira_em) <= agora()
        ):
            continue
        itens.append(
            ConviteComVaga(
                convite=_convite_para_front(convite),
                vaga=_vaga_para_front(db, vaga, usuario),
            )
        )
    return itens


@router.post("/convites/{convite_id}/aceitar", response_model=AceiteResposta)
def aceitar_convite(
    convite_id: int,
    usuario: Usuario = Depends(profissional_ativo),
    db: Session = Depends(get_db),
) -> AceiteResposta:
    """Aceita o convite em que o contratante escolheu o profissional e abre a conversa."""
    perfil = usuario.perfil_profissional
    convite = _convite_do_profissional(db, convite_id, perfil)
    vaga = db.get(Vaga, convite.vaga_id)
    if convite.estado != EstadoConvite.ESCOLHIDO or vaga.estado != EstadoVaga.AGUARDANDO_ACEITE:
        raise erro_http(
            status.HTTP_409_CONFLICT, "convite_indisponivel", "Este convite não pode ser aceito."
        )
    if convite.expira_em is not None and como_utc(convite.expira_em) <= agora():
        raise erro_http(status.HTTP_409_CONFLICT, "convite_expirado", "O prazo do convite acabou.")

    convite.estado = EstadoConvite.ACEITO
    vaga.profissional_id = perfil.id
    vaga.valor_fixado = convite.valor_proposto or vaga.valor_inicial
    vagas_servico.transicionar(
        vaga, vagas_servico.EventoVaga.ACEITAR_CONVITE, motivo="convite_aceito"
    )
    vaga.prazo_em = None
    for outro in db.scalars(
        select(Convite).where(
            Convite.vaga_id == vaga.id,
            Convite.id != convite.id,
            Convite.estado.in_(match.ESTADOS_CONVITE_ABERTO),
        )
    ):
        outro.estado = EstadoConvite.EXPIRADO
    conversa = db.scalars(select(Conversa).where(Conversa.vaga_id == vaga.id)).first()
    if conversa is None:
        conversa = Conversa(vaga_id=vaga.id)
        db.add(conversa)
    contratante = db.get(PerfilContratante, vaga.contratante_id)
    _notificar(db, contratante.usuario_id, "convite_aceito", "Convite aceito", vaga)
    db.commit()
    db.refresh(conversa)
    return AceiteResposta(vaga_id=str(vaga.id), conversa_id=str(conversa.id))


@router.post("/convites/{convite_id}/recusar", status_code=status.HTTP_204_NO_CONTENT)
def recusar_convite(
    convite_id: int,
    usuario: Usuario = Depends(profissional_ativo),
    db: Session = Depends(get_db),
) -> None:
    """Recusa o convite. Se o contratante já tinha escolhido o profissional, a vaga reabre."""
    convite = _convite_do_profissional(db, convite_id, usuario.perfil_profissional)
    if convite.estado not in match.ESTADOS_CONVITE_ABERTO:
        raise erro_http(
            status.HTTP_409_CONFLICT, "convite_indisponivel", "Este convite já foi encerrado."
        )
    foi_escolhido = convite.estado == EstadoConvite.ESCOLHIDO
    convite.estado = EstadoConvite.RECUSADO
    if foi_escolhido:
        vaga = db.get(Vaga, convite.vaga_id)
        vagas_servico.transicionar(
            vaga, vagas_servico.EventoVaga.RECUSAR_CONVITE, motivo="convite_recusado"
        )
        vaga.prazo_em = vagas_servico.calcular_prazo_publicacao(db, vaga.inicio_em)
    db.commit()


# ---------------------------------------------------------------- contratante


def _recomendado(db: Session, convite: Convite, vaga: Vaga) -> ProfissionalRecomendado:
    perfil = db.get(PerfilProfissional, convite.profissional_id)
    nota, total = reputacao_do_usuario(db, perfil.usuario_id)
    novo = match.eh_novo(db, perfil)
    destino = db.get(Endereco, vaga.endereco_id)
    distancia = (
        arredondar_km(
            distancia_km(
                perfil.endereco.latitude,
                perfil.endereco.longitude,
                destino.latitude,
                destino.longitude,
            )
        )
        if perfil.endereco
        else 0.0
    )
    resumo = (
        "Novo na plataforma."
        if perfil.servicos_concluidos == 0
        else f"{perfil.servicos_concluidos} serviços concluídos."
    )
    return ProfissionalRecomendado(
        id=str(perfil.id),
        usuario_id=perfil.usuario_id,
        nome=perfil.usuario.nome,
        foto_url=perfil.foto_url,
        verificado=perfil.verificado,
        nota_media=nota,
        total_avaliacoes=total,
        distancia_km=distancia,
        novo=novo,
        demonstrou_interesse=convite.estado
        in (EstadoConvite.INTERESSADO, EstadoConvite.ESCOLHIDO, EstadoConvite.ACEITO),
        valor_proposto=float(convite.valor_proposto) if convite.valor_proposto else None,
        resumo=resumo,
        habilidades=[c.nome for c in perfil.categorias],
        estado="falhou"
        if convite.estado in (EstadoConvite.RECUSADO, EstadoConvite.EXPIRADO)
        else "disponivel",
    )


def _garantir_rodada(db: Session, vaga: Vaga) -> None:
    """Gera a rodada quando a vaga ainda não tem convites ou quando toda a atual falhou."""
    if vaga.estado != EstadoVaga.PUBLICADA:
        return
    convites = db.scalars(select(Convite).where(Convite.vaga_id == vaga.id)).all()
    atuais = [c for c in convites if c.rodada == vaga.rodada]
    if convites and any(c.estado in match.ESTADOS_CONVITE_ABERTO for c in atuais):
        return
    match.gerar_rodada(db, vaga)


def _top3_da_vaga(db: Session, vaga: Vaga, usuario: Usuario) -> Top3Contratacao:
    _garantir_rodada(db, vaga)
    convites = db.scalars(
        select(Convite)
        .where(Convite.vaga_id == vaga.id, Convite.rodada == vaga.rodada)
        .order_by(Convite.posicao)
    ).all()
    escolhido = next(
        (c for c in convites if c.estado in (EstadoConvite.ESCOLHIDO, EstadoConvite.ACEITO)),
        None,
    )
    atual = None
    if escolhido is not None and escolhido.expira_em is not None:
        atual = ConviteContratante(
            id=str(escolhido.id),
            profissional_id=str(escolhido.profissional_id),
            status="aceito" if escolhido.estado == EstadoConvite.ACEITO else "em_andamento",
            prazo_em=como_utc(escolhido.expira_em),
        )
    return Top3Contratacao(
        vaga=_vaga_para_front(db, vaga, usuario),
        rodada=vaga.rodada,
        profissionais=[_recomendado(db, c, vaga) for c in convites],
        convite_atual=atual,
    )


@router.get("/contratante/top3", response_model=list[Top3Contratacao])
def listar_top3(
    usuario: Usuario = Depends(contratante_ativo), db: Session = Depends(get_db)
) -> list[Top3Contratacao]:
    """Top 3 atual de cada vaga do contratante que ainda está em busca de profissional."""
    vagas = db.scalars(
        select(Vaga)
        .where(
            Vaga.contratante_id == usuario.perfil_contratante.id,
            Vaga.estado.in_((EstadoVaga.PUBLICADA, EstadoVaga.AGUARDANDO_ACEITE)),
        )
        .order_by(Vaga.id.desc())
    ).all()
    return [_top3_da_vaga(db, vaga, usuario) for vaga in vagas]


@router.post("/contratante/vagas/{vaga_id}/escolher", response_model=Top3Contratacao)
def escolher_profissional(
    vaga_id: int,
    entrada: EscolhaEntrada,
    usuario: Usuario = Depends(contratante_ativo),
    db: Session = Depends(get_db),
) -> Top3Contratacao:
    """Escolhe um dos três profissionais e abre a janela de aceite."""
    vaga = _vaga_obrigatoria(db, vaga_id)
    if vaga.contratante_id != usuario.perfil_contratante.id:
        raise erro_http(
            status.HTTP_403_FORBIDDEN, "acesso_restrito", "Esta vaga pertence a outro contratante."
        )
    if vaga.estado != EstadoVaga.PUBLICADA:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "vaga_indisponivel",
            "Só é possível escolher em vagas publicadas.",
            estado_atual=vaga.estado.value,
        )
    try:
        profissional_id = int(entrada.profissional_id)
    except ValueError:
        profissional_id = -1
    convite = db.scalars(
        select(Convite).where(
            Convite.vaga_id == vaga.id,
            Convite.rodada == vaga.rodada,
            Convite.profissional_id == profissional_id,
        )
    ).first()
    if convite is None or convite.estado not in (
        EstadoConvite.SUGERIDO,
        EstadoConvite.INTERESSADO,
    ):
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "profissional_indisponivel",
            "Este profissional não está disponível na rodada atual.",
        )
    prazo = agora() + timedelta(minutes=configuracao.get("janela_aceite_min", db=db))
    convite.estado = EstadoConvite.ESCOLHIDO
    convite.expira_em = prazo
    vagas_servico.transicionar(
        vaga, vagas_servico.EventoVaga.INICIAR_CONTRATACAO, motivo="profissional_escolhido"
    )
    vaga.prazo_em = prazo
    perfil = db.get(PerfilProfissional, convite.profissional_id)
    _notificar(db, perfil.usuario_id, "escolhido_convite", "Você foi escolhido", vaga)
    db.commit()
    db.refresh(vaga)
    return _top3_da_vaga(db, vaga, usuario)
