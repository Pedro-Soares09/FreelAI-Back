"""Visão pública do perfil do profissional: reputação, selos, categorias e avaliações recebidas."""

from fastapi import status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.schemas.cadastro import CaracteristicasEntrada
from app.api.v1.schemas.perfil_contratante import AvaliacaoPublica
from app.api.v1.schemas.perfil_profissional import CategoriaPublica, PerfilPublicoProfissional
from app.erros import erro_http
from app.models import Avaliacao, PerfilProfissional, Usuario, Vaga
from app.services import avaliacoes
from app.services.cadastro import estado_verificacao
from app.tempo import agora

LIMITE_DE_AVALIACOES = 20


def perfil_publico(db: Session, usuario_id: int) -> PerfilPublicoProfissional:
    perfil = db.scalar(
        select(PerfilProfissional).where(PerfilProfissional.usuario_id == usuario_id)
    )
    if perfil is None or perfil.usuario.excluida_em is not None:
        raise erro_http(
            status.HTTP_404_NOT_FOUND,
            "perfil_nao_encontrado",
            "Perfil profissional não encontrado.",
        )
    reputacao = avaliacoes.calcular_reputacao_usuario(db, perfil.usuario)
    linhas = db.execute(
        select(Avaliacao, Usuario.nome, Vaga.titulo)
        .join(Usuario, Usuario.id == Avaliacao.avaliador_id)
        .join(Vaga, Vaga.id == Avaliacao.vaga_id)
        .where(
            Avaliacao.avaliado_id == usuario_id,
            Avaliacao.visivel_em.is_not(None),
            Avaliacao.visivel_em <= agora(),
        )
        .order_by(Avaliacao.id.desc())
        .limit(LIMITE_DE_AVALIACOES)
    ).all()
    endereco = perfil.endereco
    return PerfilPublicoProfissional(
        usuario_id=usuario_id,
        nome=perfil.usuario.nome,
        foto_url=perfil.foto_url,
        bairro=endereco.bairro if endereco else None,
        categorias=[
            CategoriaPublica(slug=c.slug, nome=c.nome, nivel_risco=c.risco)
            for c in perfil.categorias
        ],
        caracteristicas=CaracteristicasEntrada(
            comunicacao=perfil.comunicacao,
            agilidade=perfil.agilidade,
            organizacao=perfil.organizacao,
        ),
        verificado=estado_verificacao(perfil) == "verificado",
        pro=perfil.pro,
        reputacao=reputacao.reputacao,
        nota_media=reputacao.nota_media,
        total_avaliacoes=reputacao.total_avaliacoes,
        servicos_concluidos=reputacao.servicos_concluidos,
        novo=reputacao.novo,
        membro_desde=perfil.criado_em,
        avaliacoes=[
            AvaliacaoPublica(
                nota=a.nota,
                comentario=a.comentario,
                avaliador_nome=nome,
                vaga_titulo=titulo,
                criado_em=a.criado_em,
            )
            for a, nome, titulo in linhas
        ],
    )
