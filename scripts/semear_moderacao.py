"""Dados fictícios de moderação para a demonstração: denúncias, disputas e banimentos.

Chamado por `semear_demo.py` depois das categorias e das contas base. É idempotente: se a
conta marcadora já existe, nada é criado. Usa os serviços de denúncias, disputas e pagamentos
para que os estados (bloqueio de chat, banimento, lançamentos) fiquem coerentes.
Não deve ser executado em ambiente de produção.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.api.v1.schemas.disputas import DecisaoDisputaEntrada
from app.models import Categoria, Endereco, Vaga
from app.models.contratacao import (
    AceiteContrato,
    Avaliacao,
    ComprovantePagamento,
    Contrato,
    Conversa,
    Convite,
    Denuncia,
    Disputa,
    Mensagem,
    TentativaContato,
)
from app.models.enums import (
    EstadoConta,
    EstadoConvite,
    EstadoDisputa,
    EstadoVaga,
    OrigemDenuncia,
    StatusAprovacao,
    TipoArquivo,
    TipoComprovante,
    TipoPerfil,
    TipoPessoa,
)
from app.models.nucleo import Arquivo, PerfilContratante, PerfilProfissional, Usuario
from app.security import hash_password
from app.services import denuncias, disputas, pagamentos

SENHA_DEMO = "senha-segura-123"
MARCADOR = "rafael.lima@example.com"

PROFISSIONAIS = [
    ("Rafael Lima", "rafael.lima@example.com", "Casa Amarela", -8.0260, -34.9180),
    ("Tiago Alves", "tiago.alves@example.com", "Madalena", -8.0560, -34.9070),
    ("Patrícia Nunes", "patricia.nunes@example.com", "Graças", -8.0420, -34.8990),
]
CONTRATANTES = [
    ("Juliana Prado", "juliana.prado@example.com", "Boa Viagem"),
    ("Marcos Teles", "marcos.teles@example.com", "Casa Forte"),
    ("Helena Duarte", "helena.duarte@example.com", "Parnamirim"),
    ("Roberto Cunha", "roberto.cunha@example.com", "Aflitos"),
]


def _agora() -> datetime:
    return datetime.now(UTC)


def _usuario(db, nome: str, email: str) -> Usuario:
    usuario = db.query(Usuario).filter_by(email=email).one_or_none()
    if usuario is None:
        usuario = Usuario(nome=nome, email=email, senha_hash=hash_password(SENHA_DEMO))
        db.add(usuario)
        db.flush()
    usuario.estado = EstadoConta.ATIVA
    return usuario


def _profissional(db, indice: int, dados, categorias) -> PerfilProfissional:
    nome, email, bairro, lat, lon = dados
    usuario = _usuario(db, nome, email)
    if usuario.perfil_profissional is None:
        endereco = Endereco(bairro=bairro, latitude=lat, longitude=lon)
        db.add(endereco)
        db.flush()
        db.add(
            PerfilProfissional(
                usuario_id=usuario.id,
                cpf=f"9100000000{indice}",
                data_nascimento=date(1990, 7, 15),
                telefone="81999992000",
                endereco_id=endereco.id,
                raio_km=15,
                servicos_concluidos=8,
                verificacao=StatusAprovacao.APROVADO,
                experiencia_aprovada=StatusAprovacao.APROVADO,
                categorias=categorias,
            )
        )
        db.flush()
        db.refresh(usuario)
    usuario.perfil_ativo = TipoPerfil.PROFISSIONAL
    return usuario.perfil_profissional


def _contratante(db, indice: int, dados) -> PerfilContratante:
    nome, email, _bairro = dados
    usuario = _usuario(db, nome, email)
    if usuario.perfil_contratante is None:
        db.add(
            PerfilContratante(
                usuario_id=usuario.id,
                tipo_pessoa=TipoPessoa.FISICA,
                documento=f"8100000000{indice}",
                telefone="81988882000",
                verificacao=StatusAprovacao.APROVADO,
            )
        )
        db.flush()
        db.refresh(usuario)
    usuario.perfil_ativo = TipoPerfil.CONTRATANTE
    return usuario.perfil_contratante


def _vaga_com_chat(
    db,
    contratante: PerfilContratante,
    profissional: PerfilProfissional,
    categoria: Categoria,
    *,
    titulo: str,
    valor: int,
    bairro: str,
    horas_atras: int,
    estado: EstadoVaga,
    mensagens: list[tuple[str, str]],
) -> Vaga:
    """Vaga já contratada, com contrato aceito pelas duas partes e uma conversa de exemplo."""
    base = _agora() - timedelta(hours=horas_atras)
    endereco = Endereco(bairro=bairro, latitude=-8.05, longitude=-34.90)
    db.add(endereco)
    db.flush()
    vaga = Vaga(
        contratante_id=contratante.id,
        categoria_id=categoria.id,
        profissional_id=profissional.id,
        endereco_id=endereco.id,
        titulo=titulo,
        descricao=f"{titulo}. Material fornecido pelo contratante.",
        inicio_em=base,
        duracao_minutos=180,
        valor_inicial=Decimal(valor),
        valor_fixado=Decimal(valor),
        estado=estado,
        prazo_em=base - timedelta(hours=6),
    )
    db.add(vaga)
    db.flush()
    db.add(
        Convite(
            vaga_id=vaga.id,
            profissional_id=profissional.id,
            posicao=1,
            pontuacao=0.9,
            estado=EstadoConvite.ACEITO,
        )
    )
    contrato = Contrato(
        vaga_id=vaga.id,
        versao_termos="1.0",
        conteudo={"titulo": titulo, "valor": str(valor), "bairro": bairro},
        criado_em=base - timedelta(hours=5),
    )
    db.add(contrato)
    db.flush()
    for perfil, usuario_id in (
        (TipoPerfil.CONTRATANTE, contratante.usuario_id),
        (TipoPerfil.PROFISSIONAL, profissional.usuario_id),
    ):
        db.add(
            AceiteContrato(
                contrato_id=contrato.id,
                usuario_id=usuario_id,
                perfil=perfil,
                aceito_em=base - timedelta(hours=4),
            )
        )
    conversa = Conversa(vaga_id=vaga.id, criado_em=base - timedelta(hours=6))
    db.add(conversa)
    db.flush()
    autores = {"c": contratante.usuario_id, "p": profissional.usuario_id}
    for indice, (quem, texto) in enumerate(mensagens):
        db.add(
            Mensagem(
                conversa_id=conversa.id,
                autor_id=autores[quem],
                texto=texto,
                enviada_em=base - timedelta(hours=5) + timedelta(minutes=7 * indice),
            )
        )
    db.flush()
    return vaga


def _denuncia(
    db,
    *,
    denunciante: Usuario | None,
    denunciado: Usuario,
    vaga: Vaga,
    motivo: str,
    descricao: str,
    horas_em_fila: int,
    automatica: bool = False,
) -> Denuncia:
    conversa = db.query(Conversa).filter_by(vaga_id=vaga.id).one()
    denuncia = Denuncia(
        denunciante_id=denunciante.id if denunciante is not None else None,
        denunciado_id=denunciado.id,
        vaga_id=vaga.id,
        conversa_id=conversa.id,
        origem=OrigemDenuncia.AUTOMATICA if automatica else OrigemDenuncia.USUARIO,
        motivo=motivo,
        descricao=descricao,
        criado_em=_agora() - timedelta(hours=horas_em_fila),
    )
    db.add(denuncia)
    db.flush()
    return denuncia


def _disputa(
    db, vaga: Vaga, contratante: Usuario, descricao: str, horas_em_fila: int, *, reter: bool
) -> Disputa:
    """Abre a disputa direto no banco (as janelas de prazo da API não valem para a semente)."""
    pagamento = pagamentos.emitir_cobranca(db, vaga)
    if reter:
        vaga.estado = EstadoVaga.AGUARDANDO_PAGAMENTO
        db.flush()
        pagamentos.reter(db, pagamento)
    vaga.estado = EstadoVaga.EM_DISPUTA
    disputa = Disputa(
        vaga_id=vaga.id,
        aberta_por_id=contratante.id,
        descricao=descricao,
        estado=EstadoDisputa.ABERTA,
        criado_em=_agora() - timedelta(hours=horas_em_fila),
    )
    db.add(disputa)
    db.flush()
    return disputa


PERFIS = {
    "juliana": ("Moro em apartamento e contrato serviços pontuais de pintura e reparo.", "azul"),
    "marcos": ("Cuido de uma casa grande e prefiro serviços aos sábados.", "grafite"),
    "helena": ("Reformando a casa aos poucos. Pago bônus quando o serviço fica ótimo.", "verde"),
    "roberto": ("Empresário, contrato limpeza e manutenção para o escritório.", "laranja"),
}
HORARIOS = {
    "juliana": "Seg a sex, 18h às 21h",
    "marcos": "Sábados, 8h às 12h",
    "helena": "Todos os dias, 9h às 19h",
    "roberto": "Seg a sex, 8h às 17h",
}
AVALIACOES = [
    ("v5", "patricia", "helena", 5, "Pagamento em dia e conversa respeitosa."),
    ("v4", "patricia", "roberto", 2, "Chegou a me ofender durante o serviço."),
    ("v1", "rafael", "juliana", 3, "Combinado ok, mas pressionou por contato fora do app."),
    ("v7", "tiago", "marcos", 2, "Mudou o combinado e foi agressivo."),
]


def _personalizar_perfis(db, u, vagas) -> None:
    """Bio, cor, horário e avaliações visíveis para o perfil público dos contratantes."""
    for chave, (bio, cor) in PERFIS.items():
        perfil = u[chave].perfil_contratante
        perfil.bio = bio
        perfil.cor_destaque = cor
        perfil.horario_contato = HORARIOS[chave]
        perfil.bairro = next(b for _n, e, b in CONTRATANTES if u[chave].email == e)
        perfil.exigir_verificado = chave in ("helena", "roberto")
    agora_ = _agora()
    for vaga_chave, quem, para, nota, comentario in AVALIACOES:
        db.add(
            Avaliacao(
                vaga_id=vagas[vaga_chave].id,
                avaliador_id=u[quem].id,
                avaliado_id=u[para].id,
                nota=nota,
                comentario=comentario,
                criado_em=agora_ - timedelta(days=3),
                visivel_em=agora_ - timedelta(days=2),
            )
        )
    # A Helena paga bônus: ganha o selo de contratante destaque.
    arquivo = Arquivo(
        usuario_id=u["helena"].id,
        tipo=TipoArquivo.COMPROVANTE,
        chave="comprovante/semente-bonus.pdf",
        mime_type="application/pdf",
        tamanho_bytes=1024,
    )
    db.add(arquivo)
    db.flush()
    db.add(
        ComprovantePagamento(
            vaga_id=vagas["v5"].id,
            tipo=TipoComprovante.BONUS,
            valor=Decimal("30.00"),
            arquivo_id=arquivo.id,
            enviado_por_id=u["helena"].id,
            taxa_pct=Decimal("7"),
            taxa_valor=Decimal("2.10"),
        )
    )


def semear_moderacao(db, por_slug: dict[str, Categoria]) -> None:
    """Cria denúncias, disputas e banimentos de exemplo para a aba de administração."""
    if db.query(Usuario).filter_by(email=MARCADOR).one_or_none() is not None:
        return

    cats = list(por_slug.values())
    rafael, tiago, patricia = (
        _profissional(db, i, dados, cats) for i, dados in enumerate(PROFISSIONAIS)
    )
    juliana, marcos, helena, roberto = (
        _contratante(db, i, dados) for i, dados in enumerate(CONTRATANTES)
    )
    db.flush()
    u = {
        "rafael": rafael.usuario,
        "tiago": tiago.usuario,
        "patricia": patricia.usuario,
        "juliana": juliana.usuario,
        "marcos": marcos.usuario,
        "helena": helena.usuario,
        "roberto": roberto.usuario,
    }
    pintura, eletrica, jardinagem = (
        por_slug["pintura"],
        por_slug["eletrica"],
        por_slug["jardinagem"],
    )
    montagem, limpeza = por_slug["montagem"], por_slug["limpeza"]
    L = EstadoVaga.LIBERADA

    # Vagas com histórico de conversa, uma para cada situação de moderação.
    v1 = _vaga_com_chat(
        db,
        juliana,
        rafael,
        pintura,
        titulo="Pintura de quarto",
        valor=260,
        bairro="Boa Viagem",
        horas_atras=240,
        estado=L,
        mensagens=[
            ("c", "Oi Rafael, confirma amanhã às 8h?"),
            ("p", "Confirmo. Levo a tinta e os rolos."),
            ("c", "Perfeito, te espero."),
            ("c", "São 10h e você não apareceu. Posso ligar?"),
        ],
    )
    v2 = _vaga_com_chat(
        db,
        marcos,
        rafael,
        eletrica,
        titulo="Instalação de tomadas",
        valor=300,
        bairro="Casa Forte",
        horas_atras=200,
        estado=L,
        mensagens=[
            ("c", "Quantas tomadas você consegue instalar?"),
            ("p", "Todas. Mas se reclamar do preço eu vou embora."),
            ("c", "Não precisa falar assim comigo."),
            ("p", "Você que não entende nada, esse preço é o certo."),
        ],
    )
    v3 = _vaga_com_chat(
        db,
        helena,
        tiago,
        montagem,
        titulo="Montagem de estante",
        valor=190,
        bairro="Parnamirim",
        horas_atras=170,
        estado=L,
        mensagens=[
            ("c", "Pode vir sábado de manhã?"),
            ("p", "Posso. Se pagar direto comigo eu faço mais barato."),
            ("c", "Prefiro pagar pelo aplicativo."),
            ("p", "Me chama no zap que eu te passo meu pix"),
            ("p", "81 9 9999 0000, fora daqui é mais fácil"),
        ],
    )
    v4 = _vaga_com_chat(
        db,
        roberto,
        patricia,
        limpeza,
        titulo="Limpeza de apartamento",
        valor=210,
        bairro="Aflitos",
        horas_atras=30,
        estado=L,
        mensagens=[
            ("p", "Cheguei, pode abrir o portão?"),
            ("c", "Você chegou 40 minutos atrasada, isso é um absurdo."),
            ("p", "Avisei no aplicativo. Fique tranquilo, vou compensar."),
            ("c", "Vou dar nota zero para você, sua incompetente."),
        ],
    )
    v5 = _vaga_com_chat(
        db,
        helena,
        patricia,
        jardinagem,
        titulo="Poda de jardim",
        valor=150,
        bairro="Parnamirim",
        horas_atras=120,
        estado=L,
        mensagens=[
            ("c", "A poda ficou irregular na lateral."),
            ("p", "Foi feita conforme combinado e as fotos mostram o resultado."),
            ("c", "Vou denunciar mesmo assim."),
        ],
    )
    v7 = _vaga_com_chat(
        db,
        marcos,
        tiago,
        montagem,
        titulo="Montagem de guarda-roupa",
        valor=240,
        bairro="Casa Forte",
        horas_atras=150,
        estado=L,
        mensagens=[
            ("p", "Cheguei no horário combinado."),
            ("c", "Esse guarda-roupa está torto, você é um enganador."),
            ("c", "Vou espalhar para todo mundo que você é golpista."),
        ],
    )

    # Denúncias decididas procedentes: Rafael chega a duas penalidades e é banido.
    d1 = _denuncia(
        db,
        denunciante=u["juliana"],
        denunciado=u["rafael"],
        vaga=v1,
        motivo="nao_compareceu",
        descricao="Combinou o horário, não apareceu e não respondeu mais.",
        horas_em_fila=120,
    )
    d2 = _denuncia(
        db,
        denunciante=u["marcos"],
        denunciado=u["rafael"],
        vaga=v2,
        motivo="comportamento_inadequado",
        descricao="Falou de forma agressiva durante a negociação do valor.",
        horas_em_fila=100,
    )
    d3 = _denuncia(
        db,
        denunciante=u["rafael"],
        denunciado=u["juliana"],
        vaga=v1,
        motivo="comportamento_inadequado",
        descricao="Ameaçou ligar várias vezes e pressionou fora do chat.",
        horas_em_fila=110,
    )
    d4 = _denuncia(
        db,
        denunciante=u["tiago"],
        denunciado=u["marcos"],
        vaga=v7,
        motivo="comportamento_inadequado",
        descricao="Ofendeu o profissional e ameaçou espalhar acusações falsas.",
        horas_em_fila=90,
    )
    # Improcedente: a profissional entregou o serviço como combinado.
    d5 = _denuncia(
        db,
        denunciante=u["helena"],
        denunciado=u["patricia"],
        vaga=v5,
        motivo="servico_mal_feito",
        descricao="A poda ficou irregular na lateral esquerda.",
        horas_em_fila=70,
    )
    db.flush()

    # Fila aberta: uma acima da meta de 48h úteis, uma recente e uma automática por telefone.
    _denuncia(
        db,
        denunciante=u["helena"],
        denunciado=u["tiago"],
        vaga=v3,
        motivo="cobranca_fora_da_plataforma",
        descricao="Pediu para combinar o pagamento fora do aplicativo e passou um número.",
        horas_em_fila=170,
    )
    _denuncia(
        db,
        denunciante=u["patricia"],
        denunciado=u["roberto"],
        vaga=v4,
        motivo="mensagens_ofensivas",
        descricao="Chamou a profissional de incompetente e ameaçou avaliar com nota zero.",
        horas_em_fila=3,
    )
    conversa_v3 = db.query(Conversa).filter_by(vaga_id=v3.id).one()
    for texto in ("me chama no zap 81 9 9999 0000", "meu pix e o telefone acima", "81999990000"):
        db.add(
            TentativaContato(
                conversa_id=conversa_v3.id, usuario_id=tiago.usuario_id, conteudo=texto
            )
        )
    _denuncia(
        db,
        denunciante=None,
        denunciado=u["tiago"],
        vaga=v3,
        motivo="tentativas_de_contato",
        descricao="Três tentativas de enviar telefone e WhatsApp pelo chat.",
        horas_em_fila=26,
        automatica=True,
    )
    db.commit()

    for denuncia, decisao, motivo in (
        (d1, "procedente", "Ausência confirmada pelo contratante e pelo histórico do chat."),
        (d2, "procedente", "Linguagem agressiva registrada no chat. Segunda penalidade."),
        (d3, "procedente", "Pressão por contato fora da plataforma comprovada."),
        (d4, "procedente", "Ofensas e ameaça registradas no chat."),
        (d5, "improcedente", "Fotos do chat mostram a poda conforme o combinado."),
    ):
        denuncias.decidir_denuncia(db, denuncia.id, decisao=decisao, motivo=motivo)

    # Disputas: duas antes do pagamento, uma com valor retido e uma já decidida.
    v8 = _vaga_com_chat(
        db,
        roberto,
        patricia,
        jardinagem,
        titulo="Poda de árvores no quintal",
        valor=320,
        bairro="Aflitos",
        horas_atras=26,
        estado=EstadoVaga.CONCLUIDA,
        mensagens=[("p", "Terminei a poda."), ("c", "Ficou faltando a árvore dos fundos.")],
    )
    v9 = _vaga_com_chat(
        db,
        helena,
        tiago,
        montagem,
        titulo="Montagem de cozinha planejada",
        valor=480,
        bairro="Parnamirim",
        horas_atras=30,
        estado=EstadoVaga.CONCLUIDA,
        mensagens=[("p", "Cozinha montada."), ("c", "Duas portas não fecham direito.")],
    )
    v10 = _vaga_com_chat(
        db,
        juliana,
        patricia,
        limpeza,
        titulo="Limpeza pós-obra",
        valor=260,
        bairro="Boa Viagem",
        horas_atras=28,
        estado=EstadoVaga.CONCLUIDA,
        mensagens=[("p", "Limpeza concluída."), ("c", "Ainda tem entulho na varanda.")],
    )
    v11 = _vaga_com_chat(
        db,
        marcos,
        rafael,
        pintura,
        titulo="Pintura de fachada",
        valor=600,
        bairro="Casa Forte",
        horas_atras=70,
        estado=EstadoVaga.CONCLUIDA,
        mensagens=[
            ("p", "Fachada pintada."),
            ("c", "Pintou só uma demão e a parede ficou manchada."),
        ],
    )
    _disputa(db, v8, u["roberto"], "A poda da árvore dos fundos não foi feita.", 6, reter=False)
    _disputa(db, v9, u["helena"], "Duas portas e uma gaveta ficaram desalinhadas.", 20, reter=True)
    _disputa(
        db, v10, u["juliana"], "Sobrou entulho na varanda e o piso ficou sujo.", 2, reter=False
    )
    d_decidida = _disputa(
        db, v11, u["marcos"], "Só uma demão de tinta e manchas na parede.", 52, reter=True
    )
    db.commit()
    disputas.decidir_disputa(
        db,
        d_decidida.id,
        DecisaoDisputaEntrada(
            decisao="reembolso_parcial",
            percentual_reembolso=40,
            motivo="Fotos mostram uma demão e manchas. Reembolso de 40% e 60% ao profissional.",
        ),
    )
    _personalizar_perfis(db, u, {"v1": v1, "v4": v4, "v5": v5, "v7": v7})
    db.commit()
    print("Moderação de demonstração criada (denúncias, disputas e banimento).")
