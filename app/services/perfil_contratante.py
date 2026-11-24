"""Edição e visão pública do perfil do contratante."""

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.schemas.perfil_contratante import (
    AtualizacaoContratanteEntrada,
    AvaliacaoPublica,
    CategoriaDeInteresse,
    PerfilPublicoContratante,
)
from app.erros import erro_http
from app.models import Arquivo, Avaliacao, Categoria, PerfilContratante, TipoArquivo, Usuario, Vaga
from app.models.enums import EstadoVaga
from app.services import arquivos, comprovantes
from app.tempo import agora

ESTADOS_CONCLUIDOS = (EstadoVaga.CONCLUIDA, EstadoVaga.LIBERADA, EstadoVaga.LIBERADA_PARCIAL)
MIMES_AVATAR = ("image/jpeg", "image/png")
AVATAR_MAX_BYTES = 2 * 1024 * 1024


def _slugs_validos(db: Session, slugs: list[str]) -> list[str]:
    unicos = list(dict.fromkeys(slugs))
    existentes = set(db.scalars(select(Categoria.slug).where(Categoria.slug.in_(unicos))).all())
    faltando = [s for s in unicos if s not in existentes]
    if faltando:
        raise erro_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "categoria_inexistente",
            "Há categorias que não existem.",
            categorias=faltando,
        )
    return unicos


def _avatar_do_usuario(db: Session, usuario: Usuario, arquivo_id: int) -> str:
    arquivo = db.get(Arquivo, arquivo_id)
    if arquivo is None or arquivo.usuario_id != usuario.id or arquivo.tipo != TipoArquivo.FOTO:
        raise erro_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "avatar_invalido",
            "Envie uma foto sua pelo envio de arquivos e use o identificador devolvido.",
        )
    if arquivo.mime_type not in MIMES_AVATAR:
        raise erro_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "avatar_formato_invalido",
            "O avatar precisa ser uma imagem JPG ou PNG.",
        )
    if arquivo.tamanho_bytes > AVATAR_MAX_BYTES:
        raise erro_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "avatar_grande_demais",
            "O avatar pode ter no máximo 2 MB.",
        )
    return arquivos.url_do_arquivo(arquivo.chave)


def atualizar(
    db: Session, perfil: PerfilContratante, entrada: AtualizacaoContratanteEntrada
) -> PerfilContratante:
    dados = entrada.model_dump(exclude_unset=True)
    for campo in ("bio", "bairro", "horario_contato"):
        if campo in dados:
            setattr(perfil, campo, dados[campo])
    for campo in ("exigir_verificado", "aceita_notificacoes", "cor_destaque"):
        if dados.get(campo) is not None:
            setattr(perfil, campo, dados[campo])
    if dados.get("categorias_interesse") is not None:
        perfil.categorias_interesse = _slugs_validos(db, dados["categorias_interesse"])
    if "avatar_arquivo_id" in dados and dados["avatar_arquivo_id"] is not None:
        arquivo_id = dados["avatar_arquivo_id"]
        perfil.avatar_url = (
            None if arquivo_id == 0 else _avatar_do_usuario(db, perfil.usuario, arquivo_id)
        )
    db.commit()
    db.refresh(perfil)
    return perfil


def perfil_publico(db: Session, usuario_id: int) -> PerfilPublicoContratante:
    perfil = db.scalar(select(PerfilContratante).where(PerfilContratante.usuario_id == usuario_id))
    if perfil is None or perfil.usuario.excluida_em is not None:
        raise erro_http(
            status.HTTP_404_NOT_FOUND, "perfil_nao_encontrado", "Perfil contratante não encontrado."
        )
    instante = agora()
    avaliacoes = db.execute(
        select(Avaliacao, Usuario.nome, Vaga.titulo)
        .join(Usuario, Usuario.id == Avaliacao.avaliador_id)
        .join(Vaga, Vaga.id == Avaliacao.vaga_id)
        .where(
            Avaliacao.avaliado_id == usuario_id,
            Avaliacao.visivel_em.is_not(None),
            Avaliacao.visivel_em <= instante,
        )
        .order_by(Avaliacao.id.desc())
        .limit(20)
    ).all()
    media, total = db.execute(
        select(func.avg(Avaliacao.nota), func.count(Avaliacao.id)).where(
            Avaliacao.avaliado_id == usuario_id,
            Avaliacao.visivel_em.is_not(None),
            Avaliacao.visivel_em <= instante,
        )
    ).one()
    publicadas = db.scalar(select(func.count(Vaga.id)).where(Vaga.contratante_id == perfil.id))
    concluidos = db.scalar(
        select(func.count(Vaga.id)).where(
            Vaga.contratante_id == perfil.id, Vaga.estado.in_(ESTADOS_CONCLUIDOS)
        )
    )
    slugs = list(perfil.categorias_interesse or [])
    nomes = (
        {
            c.slug: c.nome
            for c in db.scalars(select(Categoria).where(Categoria.slug.in_(slugs))).all()
        }
        if slugs
        else {}
    )
    destaque = comprovantes.destaque_do_contratante(db, usuario_id)
    return PerfilPublicoContratante(
        usuario_id=usuario_id,
        nome=perfil.razao_social or perfil.usuario.nome,
        tipo_pessoa=perfil.tipo_pessoa,
        bio=perfil.bio,
        bairro=perfil.bairro,
        horario_contato=perfil.horario_contato,
        categorias_interesse=[
            CategoriaDeInteresse(slug=s, nome=nomes[s]) for s in slugs if s in nomes
        ],
        exigir_verificado=perfil.exigir_verificado,
        cor_destaque=perfil.cor_destaque,
        avatar_url=perfil.avatar_url,
        verificado=perfil.verificado,
        nota_media=round(float(media), 1) if media is not None else None,
        total_avaliacoes=int(total or 0),
        adicionais_pagos=destaque.adicionais_pagos,
        destaque=destaque.destaque,
        vagas_publicadas=int(publicadas or 0),
        servicos_concluidos=int(concluidos or 0),
        membro_desde=perfil.criado_em,
        avaliacoes=[
            AvaliacaoPublica(
                nota=a.nota,
                comentario=a.comentario,
                avaliador_nome=nome,
                vaga_titulo=titulo,
                criado_em=a.criado_em,
            )
            for a, nome, titulo in avaliacoes
        ],
    )
