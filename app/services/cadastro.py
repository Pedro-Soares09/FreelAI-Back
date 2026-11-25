"""Cadastro dos perfis, verificação de identidade, comprovações e aprovações da administração."""

from fastapi import status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.schemas.cadastro import (
    ArquivoDaFila,
    AtualizacaoContratanteEntrada,
    AtualizacaoProfissionalEntrada,
    CadastroContratanteEntrada,
    CadastroProfissionalEntrada,
    CaracteristicasEntrada,
    CategoriaDoPerfil,
    ComprovacaoDetalhe,
    EpiEntrada,
    EstadoVerificacao,
    ItemFilaAprovacao,
    PerfilContratanteDetalhe,
    PerfilProfissionalDetalhe,
    QualificacaoEntrada,
    StatusVerificacao,
    VerificacaoEntrada,
    cnpj_valido,
    cpf_valido,
)
from app.erros import erro_http
from app.models import (
    AceiteTermos,
    Arquivo,
    Categoria,
    Endereco,
    EpiProfissional,
    EstadoConta,
    Experiencia,
    FinalidadeAceite,
    PerfilContratante,
    PerfilProfissional,
    Qualificacao,
    StatusAprovacao,
    TipoArquivo,
    TipoPerfil,
    Usuario,
)
from app.services import arquivos, bairros, comprovantes, match
from app.tempo import agora

VERSAO_CONSENTIMENTO = "1.0"


# ---------------------------------------------------------------- estado da verificação


def estado_verificacao(perfil: PerfilProfissional | PerfilContratante) -> EstadoVerificacao:
    """Pendente (nada enviado), em análise, verificado ou recusado."""
    if perfil.verificacao == StatusAprovacao.APROVADO:
        return "verificado"
    if perfil.verificacao == StatusAprovacao.RECUSADO:
        return "recusado"
    return "em_analise" if perfil.verificacao_enviada_em is not None else "pendente"


# ---------------------------------------------------------------- categorias e pendências


def _categorias_por_slug(db: Session, slugs: list[str]) -> list[Categoria]:
    unicos = list(dict.fromkeys(slugs))
    encontradas = db.scalars(select(Categoria).where(Categoria.slug.in_(unicos))).all()
    por_slug = {c.slug: c for c in encontradas}
    faltando = [s for s in unicos if s not in por_slug]
    if faltando:
        raise erro_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "categoria_inexistente",
            "Há categorias que não existem.",
            categorias=faltando,
        )
    return [por_slug[s] for s in unicos]


pendencias_da_categoria = match.pendencias_da_categoria


# ---------------------------------------------------------------- montagem das respostas


def detalhar_profissional(db: Session, perfil: PerfilProfissional) -> PerfilProfissionalDetalhe:
    endereco = perfil.endereco
    return PerfilProfissionalDetalhe(
        id=perfil.id,
        usuario_id=perfil.usuario_id,
        nome=perfil.usuario.nome,
        cpf=perfil.cpf,
        data_nascimento=perfil.data_nascimento,
        telefone=perfil.telefone,
        foto_url=perfil.foto_url,
        bairro=endereco.bairro if endereco else None,
        latitude=endereco.latitude if endereco else None,
        longitude=endereco.longitude if endereco else None,
        raio_km=perfil.raio_km,
        categorias=[
            CategoriaDoPerfil(
                slug=c.slug,
                nome=c.nome,
                nivel_risco=c.risco,
                pendencias=pendencias_da_categoria(db, perfil, c),
            )
            for c in perfil.categorias
        ],
        caracteristicas=CaracteristicasEntrada(
            comunicacao=perfil.comunicacao,
            agilidade=perfil.agilidade,
            organizacao=perfil.organizacao,
        ),
        verificacao=estado_verificacao(perfil),
        verificacao_motivo=perfil.verificacao_motivo,
        experiencia_aprovada=perfil.experiencia_aprovada,
        pro=perfil.pro,
    )


def detalhar_contratante(db: Session, perfil: PerfilContratante) -> PerfilContratanteDetalhe:
    destaque = comprovantes.destaque_do_contratante(db, perfil.usuario_id)
    return PerfilContratanteDetalhe(
        id=perfil.id,
        usuario_id=perfil.usuario_id,
        nome=perfil.usuario.nome,
        email=perfil.usuario.email,
        tipo_pessoa=perfil.tipo_pessoa,
        documento=perfil.documento,
        razao_social=perfil.razao_social,
        telefone=perfil.telefone,
        selfie_url=perfil.selfie_url,
        verificacao=estado_verificacao(perfil),
        verificacao_motivo=perfil.verificacao_motivo,
        adicionais_pagos=destaque.adicionais_pagos,
        destaque=destaque.destaque,
    )


# ---------------------------------------------------------------- cadastro


def _perfil_ativo_inicial(db: Session, usuario: Usuario, tipo: TipoPerfil) -> None:
    if usuario.perfil_ativo is None:
        usuario.perfil_ativo = tipo


def criar_perfil_profissional(
    db: Session, usuario: Usuario, entrada: CadastroProfissionalEntrada
) -> PerfilProfissional:
    if usuario.perfil_profissional is not None:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "perfil_ja_cadastrado",
            "Esta conta já tem perfil profissional.",
        )
    categorias = _categorias_por_slug(db, entrada.categorias)
    latitude, longitude = bairros.resolver_coordenadas(
        entrada.bairro.strip(), entrada.latitude, entrada.longitude
    )
    endereco = Endereco(
        logradouro=entrada.logradouro,
        bairro=entrada.bairro.strip(),
        latitude=latitude,
        longitude=longitude,
    )
    db.add(endereco)
    db.flush()
    carac = entrada.caracteristicas or CaracteristicasEntrada()
    perfil = PerfilProfissional(
        usuario_id=usuario.id,
        cpf=entrada.cpf,
        data_nascimento=entrada.data_nascimento,
        telefone=entrada.telefone,
        foto_url=entrada.foto_url,
        endereco_id=endereco.id,
        raio_km=entrada.raio_km,
        comunicacao=carac.comunicacao,
        agilidade=carac.agilidade,
        organizacao=carac.organizacao,
        categorias=categorias,
    )
    db.add(perfil)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise erro_http(
            status.HTTP_409_CONFLICT, "cpf_em_uso", "Este CPF já está cadastrado."
        ) from None
    por_slug = {c.slug: c for c in categorias}
    for exp in entrada.experiencias:
        categoria = por_slug.get(exp.categoria) if exp.categoria else None
        if exp.categoria and categoria is None:
            categoria = _categorias_por_slug(db, [exp.categoria])[0]
        db.add(
            Experiencia(
                profissional_id=perfil.id,
                categoria_id=categoria.id if categoria else None,
                descricao=exp.descricao,
                referencia_nome=exp.referencia_nome,
                referencia_contato=exp.referencia_contato,
                arquivo_url=exp.arquivo_url,
            )
        )
    _perfil_ativo_inicial(db, usuario, TipoPerfil.PROFISSIONAL)
    db.commit()
    db.refresh(perfil)
    return perfil


def criar_perfil_contratante(
    db: Session, usuario: Usuario, entrada: CadastroContratanteEntrada
) -> PerfilContratante:
    if usuario.perfil_contratante is not None:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "perfil_ja_cadastrado",
            "Esta conta já tem perfil contratante.",
        )
    perfil = PerfilContratante(
        usuario_id=usuario.id,
        tipo_pessoa=entrada.tipo_pessoa,
        documento=entrada.documento,
        razao_social=entrada.razao_social,
        telefone=entrada.telefone,
        selfie_url=entrada.selfie_url,
    )
    db.add(perfil)
    _perfil_ativo_inicial(db, usuario, TipoPerfil.CONTRATANTE)
    db.commit()
    db.refresh(perfil)
    return perfil


def atualizar_perfil_profissional(
    db: Session, perfil: PerfilProfissional, entrada: AtualizacaoProfissionalEntrada
) -> PerfilProfissional:
    dados = entrada.model_dump(exclude_unset=True)

    if dados.get("nome") is not None:
        perfil.usuario.nome = dados["nome"]

    if dados.get("cpf") is not None and dados["cpf"] != perfil.cpf:
        cpf_em_uso = db.scalar(
            select(PerfilProfissional.id).where(
                PerfilProfissional.cpf == dados["cpf"], PerfilProfissional.id != perfil.id
            )
        )
        if cpf_em_uso is not None:
            raise erro_http(status.HTTP_409_CONFLICT, "cpf_em_uso", "Este CPF já está cadastrado.")
        perfil.cpf = dados["cpf"]

    if dados.get("data_nascimento") is not None:
        perfil.data_nascimento = dados["data_nascimento"]
    if "telefone" in dados and dados["telefone"] is not None:
        perfil.telefone = dados["telefone"]
    if "foto_url" in dados:
        perfil.foto_url = dados["foto_url"]

    campos_endereco = {"bairro", "logradouro", "latitude", "longitude"}
    if campos_endereco.intersection(dados):
        endereco = perfil.endereco
        if endereco is None:
            if (
                not dados.get("bairro")
                or dados.get("latitude") is None
                or dados.get("longitude") is None
            ):
                raise erro_http(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "localizacao_obrigatoria",
                    "Informe bairro, latitude e longitude.",
                    campo="latitude",
                )
            endereco = Endereco(
                bairro=dados["bairro"].strip(),
                logradouro=dados.get("logradouro"),
                latitude=dados["latitude"],
                longitude=dados["longitude"],
            )
            db.add(endereco)
            perfil.endereco = endereco
        else:
            if "bairro" in dados and dados["bairro"] is not None:
                endereco.bairro = dados["bairro"].strip()
            if "logradouro" in dados:
                endereco.logradouro = dados["logradouro"]
            if "latitude" in dados:
                endereco.latitude = dados["latitude"]
            if "longitude" in dados:
                endereco.longitude = dados["longitude"]

    if dados.get("raio_km") is not None:
        from app.services import configuracao

        perfil.raio_km = min(dados["raio_km"], configuracao.get("raio_maximo_km", db=db))
    if dados.get("categorias") is not None:
        perfil.categorias = _categorias_por_slug(db, dados["categorias"])
    if entrada.caracteristicas is not None:
        for campo in ("comunicacao", "agilidade", "organizacao"):
            setattr(perfil, campo, getattr(entrada.caracteristicas, campo))

    db.commit()
    db.refresh(perfil)
    return perfil


def atualizar_perfil_contratante(
    db: Session, perfil: PerfilContratante, entrada: AtualizacaoContratanteEntrada
) -> PerfilContratante:
    dados = entrada.model_dump(exclude_unset=True)

    if dados.get("nome") is not None:
        perfil.usuario.nome = dados["nome"]

    if "documento" in dados:
        documento = dados["documento"]
        if perfil.tipo_pessoa.value == "pf":
            if not documento or not cpf_valido(documento):
                raise erro_http(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "cpf_invalido",
                    "Informe um CPF válido.",
                    campo="documento",
                )
        elif documento and not cnpj_valido(documento):
            raise erro_http(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "cnpj_invalido",
                "Informe um CNPJ válido.",
                campo="documento",
            )
        perfil.documento = documento or None

    if "razao_social" in dados:
        perfil.razao_social = dados["razao_social"].strip() if dados["razao_social"] else None
    if "telefone" in dados and dados["telefone"] is not None:
        perfil.telefone = dados["telefone"]
    if "selfie_url" in dados:
        perfil.selfie_url = dados["selfie_url"]

    db.commit()
    db.refresh(perfil)
    return perfil


# ---------------------------------------------------------------- verificação de identidade


def _perfil_ativo(usuario: Usuario) -> tuple[TipoPerfil, PerfilProfissional | PerfilContratante]:
    if usuario.perfil_ativo == TipoPerfil.PROFISSIONAL and usuario.perfil_profissional:
        return TipoPerfil.PROFISSIONAL, usuario.perfil_profissional
    if usuario.perfil_ativo == TipoPerfil.CONTRATANTE and usuario.perfil_contratante:
        return TipoPerfil.CONTRATANTE, usuario.perfil_contratante
    raise erro_http(
        status.HTTP_409_CONFLICT,
        "perfil_nao_cadastrado",
        "Cadastre um perfil antes de enviar a verificação.",
    )


def _ultimo_arquivo(db: Session, usuario_id: int, tipo: TipoArquivo) -> Arquivo | None:
    return db.scalars(
        select(Arquivo)
        .where(Arquivo.usuario_id == usuario_id, Arquivo.tipo == tipo)
        .order_by(Arquivo.id.desc())
    ).first()


def _tem_consentimento(db: Session, usuario_id: int) -> bool:
    return (
        db.scalar(
            select(AceiteTermos.id).where(
                AceiteTermos.usuario_id == usuario_id,
                AceiteTermos.finalidade == FinalidadeAceite.BIOMETRIA,
            )
        )
        is not None
    )


def registrar_consentimento(db: Session, usuario: Usuario) -> StatusVerificacao:
    if not _tem_consentimento(db, usuario.id):
        db.add(
            AceiteTermos(
                usuario_id=usuario.id,
                finalidade=FinalidadeAceite.BIOMETRIA,
                versao=VERSAO_CONSENTIMENTO,
            )
        )
        db.commit()
    return status_da_verificacao(db, usuario)


def status_da_verificacao(db: Session, usuario: Usuario) -> StatusVerificacao:
    tipo, perfil = _perfil_ativo(usuario)
    return StatusVerificacao(
        perfil=tipo.value,
        status=estado_verificacao(perfil),
        motivo=perfil.verificacao_motivo,
        consentimento_biometria=_tem_consentimento(db, usuario.id),
        documento_enviado=_ultimo_arquivo(db, usuario.id, TipoArquivo.DOCUMENTO) is not None,
        selfie_enviada=_ultimo_arquivo(db, usuario.id, TipoArquivo.SELFIE) is not None,
        enviada_em=perfil.verificacao_enviada_em,
    )


def enviar_verificacao(
    db: Session, usuario: Usuario, entrada: VerificacaoEntrada
) -> StatusVerificacao:
    _, perfil = _perfil_ativo(usuario)
    if not _tem_consentimento(db, usuario.id):
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "consentimento_pendente",
            "Aceite o consentimento de uso de biometria antes de enviar os arquivos.",
        )
    if estado_verificacao(perfil) in ("em_analise", "verificado"):
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "verificacao_ja_enviada",
            "A verificação já foi enviada ou aprovada.",
        )
    for arquivo_id, tipo in (
        (entrada.documento_arquivo_id, TipoArquivo.DOCUMENTO),
        (entrada.selfie_arquivo_id, TipoArquivo.SELFIE),
    ):
        arquivo = db.get(Arquivo, arquivo_id)
        if arquivo is None or arquivo.usuario_id != usuario.id or arquivo.tipo != tipo:
            raise erro_http(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "arquivo_invalido",
                f"O arquivo informado não é um {tipo.value} enviado por você.",
            )
    perfil.verificacao = StatusAprovacao.PENDENTE
    perfil.verificacao_motivo = None
    perfil.verificacao_enviada_em = agora()
    db.commit()
    return status_da_verificacao(db, usuario)


# ---------------------------------------------------------------- comprovações do profissional


def _perfil_profissional(usuario: Usuario) -> PerfilProfissional:
    if usuario.perfil_profissional is None:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "perfil_nao_cadastrado",
            "Cadastre o perfil profissional primeiro.",
        )
    return usuario.perfil_profissional


def registrar_qualificacao(
    db: Session, usuario: Usuario, entrada: QualificacaoEntrada
) -> Qualificacao:
    perfil = _perfil_profissional(usuario)
    categoria = _categorias_por_slug(db, [entrada.categoria])[0]
    registro = Qualificacao(
        profissional_id=perfil.id,
        categoria_id=categoria.id,
        tipo=entrada.tipo,
        arquivo_url=entrada.arquivo_url,
        referencia=entrada.referencia,
    )
    db.add(registro)
    db.commit()
    db.refresh(registro)
    return registro


def registrar_epi(db: Session, usuario: Usuario, entrada: EpiEntrada) -> EpiProfissional:
    perfil = _perfil_profissional(usuario)
    categoria = _categorias_por_slug(db, [entrada.categoria])[0]
    registro = EpiProfissional(
        profissional_id=perfil.id, categoria_id=categoria.id, arquivo_url=entrada.arquivo_url
    )
    db.add(registro)
    db.commit()
    db.refresh(registro)
    return registro


def registrar_experiencia(db: Session, usuario: Usuario, entrada) -> Experiencia:
    perfil = _perfil_profissional(usuario)
    categoria = _categorias_por_slug(db, [entrada.categoria])[0] if entrada.categoria else None
    registro = Experiencia(
        profissional_id=perfil.id,
        categoria_id=categoria.id if categoria else None,
        descricao=entrada.descricao,
        referencia_nome=entrada.referencia_nome,
        referencia_contato=entrada.referencia_contato,
        arquivo_url=entrada.arquivo_url,
    )
    db.add(registro)
    db.commit()
    db.refresh(registro)
    return registro


def listar_comprovacoes(db: Session, usuario: Usuario) -> list[ComprovacaoDetalhe]:
    perfil = _perfil_profissional(usuario)
    slugs = {c.id: c.slug for c in db.scalars(select(Categoria)).all()}
    itens: list[ComprovacaoDetalhe] = []
    for e in db.scalars(select(Experiencia).where(Experiencia.profissional_id == perfil.id)):
        itens.append(
            ComprovacaoDetalhe(
                id=e.id,
                tipo="experiencia",
                categoria=slugs.get(e.categoria_id),
                descricao=e.descricao,
                arquivo_url=e.arquivo_url,
                status=e.status,
            )
        )
    for q in db.scalars(select(Qualificacao).where(Qualificacao.profissional_id == perfil.id)):
        itens.append(
            ComprovacaoDetalhe(
                id=q.id,
                tipo="qualificacao",
                categoria=slugs.get(q.categoria_id),
                descricao=q.referencia,
                arquivo_url=q.arquivo_url,
                status=q.status,
                motivo_recusa=q.motivo_recusa,
            )
        )
    for p in db.scalars(
        select(EpiProfissional).where(EpiProfissional.profissional_id == perfil.id)
    ):
        itens.append(
            ComprovacaoDetalhe(
                id=p.id,
                tipo="epi",
                categoria=slugs.get(p.categoria_id),
                arquivo_url=p.arquivo_url,
                status=p.status,
                motivo_recusa=p.motivo_recusa,
            )
        )
    return itens


# ---------------------------------------------------------------- aprovações da administração


def _arquivos_do_usuario(db: Session, usuario_id: int) -> list[ArquivoDaFila]:
    itens = []
    for tipo in (TipoArquivo.DOCUMENTO, TipoArquivo.SELFIE):
        arquivo = _ultimo_arquivo(db, usuario_id, tipo)
        if arquivo is not None:
            itens.append(ArquivoDaFila(tipo=tipo.value, url=arquivos.url_do_arquivo(arquivo.chave)))
    return itens


def listar_fila(
    db: Session, *, tipo: str | None, estado: StatusAprovacao | None
) -> list[ItemFilaAprovacao]:
    itens: list[ItemFilaAprovacao] = []

    def aceita(item_tipo: str, item_estado: StatusAprovacao) -> bool:
        return (tipo is None or tipo == item_tipo) and (estado is None or estado == item_estado)

    if tipo in (None, "profissional"):
        for p in db.scalars(select(PerfilProfissional).order_by(PerfilProfissional.id)):
            if p.verificacao_enviada_em is None and p.verificacao == StatusAprovacao.PENDENTE:
                continue
            if aceita("profissional", p.verificacao):
                itens.append(
                    ItemFilaAprovacao(
                        tipo="profissional",
                        id=p.id,
                        usuario_id=p.usuario_id,
                        usuario_nome=p.usuario.nome,
                        descricao="Verificação de identidade do profissional",
                        status=p.verificacao,
                        enviado_em=p.verificacao_enviada_em,
                        arquivos=_arquivos_do_usuario(db, p.usuario_id),
                    )
                )
    if tipo in (None, "contratante"):
        for c in db.scalars(select(PerfilContratante).order_by(PerfilContratante.id)):
            if c.verificacao_enviada_em is None and c.verificacao == StatusAprovacao.PENDENTE:
                continue
            if aceita("contratante", c.verificacao):
                itens.append(
                    ItemFilaAprovacao(
                        tipo="contratante",
                        id=c.id,
                        usuario_id=c.usuario_id,
                        usuario_nome=c.usuario.nome,
                        descricao="Verificação de identidade do contratante",
                        status=c.verificacao,
                        enviado_em=c.verificacao_enviada_em,
                        arquivos=_arquivos_do_usuario(db, c.usuario_id),
                    )
                )
    for tipo_item, modelo in (
        ("experiencia", Experiencia),
        ("qualificacao", Qualificacao),
        ("epi", EpiProfissional),
    ):
        if tipo not in (None, tipo_item):
            continue
        for r in db.scalars(select(modelo).order_by(modelo.id)):
            if not aceita(tipo_item, r.status):
                continue
            perfil = db.get(PerfilProfissional, r.profissional_id)
            descricao = getattr(r, "descricao", None) or getattr(r, "referencia", None)
            itens.append(
                ItemFilaAprovacao(
                    tipo=tipo_item,
                    id=r.id,
                    usuario_id=perfil.usuario_id,
                    usuario_nome=perfil.usuario.nome,
                    descricao=descricao or f"Comprovação de {tipo_item}",
                    status=r.status,
                    arquivos=(
                        [ArquivoDaFila(tipo=tipo_item, url=r.arquivo_url)] if r.arquivo_url else []
                    ),
                )
            )
    return itens


def _recalcular_experiencia(db: Session, perfil: PerfilProfissional) -> None:
    experiencias = db.scalars(
        select(Experiencia).where(Experiencia.profissional_id == perfil.id)
    ).all()
    if any(e.status == StatusAprovacao.APROVADO for e in experiencias):
        perfil.experiencia_aprovada = StatusAprovacao.APROVADO
    elif experiencias and all(e.status == StatusAprovacao.RECUSADO for e in experiencias):
        perfil.experiencia_aprovada = StatusAprovacao.RECUSADO
    else:
        perfil.experiencia_aprovada = StatusAprovacao.PENDENTE


def _ativar_conta_se_pendente(usuario: Usuario) -> None:
    if usuario.estado == EstadoConta.PENDENTE:
        usuario.estado = EstadoConta.ATIVA


def decidir_aprovacao(
    db: Session, tipo: str, item_id: int, *, aprovar: bool, motivo: str | None
) -> ItemFilaAprovacao:
    novo = StatusAprovacao.APROVADO if aprovar else StatusAprovacao.RECUSADO
    if tipo in ("profissional", "contratante"):
        modelo = PerfilProfissional if tipo == "profissional" else PerfilContratante
        perfil = db.get(modelo, item_id)
        if perfil is None or estado_verificacao(perfil) not in ("em_analise",):
            raise erro_http(
                status.HTTP_404_NOT_FOUND,
                "item_nao_encontrado",
                "Não há verificação em análise para este item.",
            )
        perfil.verificacao = novo
        perfil.verificacao_motivo = None if aprovar else motivo
        if aprovar:
            _ativar_conta_se_pendente(perfil.usuario)
    else:
        modelo = {"experiencia": Experiencia, "qualificacao": Qualificacao, "epi": EpiProfissional}[
            tipo
        ]
        registro = db.get(modelo, item_id)
        if registro is None or registro.status != StatusAprovacao.PENDENTE:
            raise erro_http(
                status.HTTP_404_NOT_FOUND,
                "item_nao_encontrado",
                "Não há comprovação pendente com este identificador.",
            )
        registro.status = novo
        if hasattr(registro, "motivo_recusa"):
            registro.motivo_recusa = None if aprovar else motivo
        if tipo == "experiencia":
            _recalcular_experiencia(db, db.get(PerfilProfissional, registro.profissional_id))
    db.commit()
    fila = listar_fila(db, tipo=tipo, estado=None)
    return next(i for i in fila if i.id == item_id)
