"""Testes do módulo de upload de arquivos: validação, armazenamento e controle de acesso."""

from io import BytesIO
from pathlib import Path

import pytest

from app.config import settings
from app.models import Configuracao, EstadoConta, PapelUsuario, TipoArquivo
from app.services import arquivos
from app.services.arquivos import BYTES_POR_MB, ArmazenamentoLocal
from tests.fabricas import cabecalho, criar_usuario

ROTA_UPLOAD = "/api/v1/arquivos"

BYTES_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
BYTES_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
BYTES_PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF"


def _enviar(
    cliente,
    usuario,
    *,
    tipo: str = "foto",
    nome: str = "retrato.jpg",
    conteudo: bytes = BYTES_JPEG,
    mime: str = "image/jpeg",
):
    return cliente.post(
        ROTA_UPLOAD,
        data={"tipo": tipo},
        files={"arquivo": (nome, BytesIO(conteudo), mime)},
        headers=cabecalho(usuario) if usuario is not None else {},
    )


# --- Autenticação e conta ----------------------------------------------------------------


def test_upload_exige_autenticacao(cliente):
    resposta = _enviar(cliente, None)

    assert resposta.status_code == 401
    assert resposta.json()["detail"]["codigo"] == "nao_autenticado"


def test_conta_pendente_pode_enviar_documentos_do_cadastro(cliente, db):
    pendente = criar_usuario(db, estado=EstadoConta.PENDENTE)

    for tipo, nome, conteudo, mime in (
        ("foto", "rosto.jpg", BYTES_JPEG, "image/jpeg"),
        ("selfie", "selfie.png", BYTES_PNG, "image/png"),
        ("documento", "rg.pdf", BYTES_PDF, "application/pdf"),
    ):
        resposta = _enviar(cliente, pendente, tipo=tipo, nome=nome, conteudo=conteudo, mime=mime)
        assert resposta.status_code == 201, tipo


def test_conta_suspensa_ou_banida_nao_faz_upload(cliente, db):
    suspensa = criar_usuario(db, estado=EstadoConta.SUSPENSA)
    banida = criar_usuario(db, estado=EstadoConta.BANIDA)

    assert _enviar(cliente, suspensa).status_code == 403
    assert _enviar(cliente, banida).status_code == 403


# --- Formatos permitidos e geração de nome -----------------------------------------------


@pytest.mark.parametrize(
    ("tipo", "nome_original", "conteudo", "mime_enviado", "ext_esperada", "mime_esperado"),
    [
        ("foto", "meu_rosto.jpg", BYTES_JPEG, "image/jpeg", ".jpg", "image/jpeg"),
        ("selfie", "Selfie CNH.jpeg", BYTES_JPEG, "image/jpeg", ".jpg", "image/jpeg"),
        ("epi", "capacete_novo.png", BYTES_PNG, "image/png", ".png", "image/png"),
        (
            "qualificacao",
            "certificado_nr10.pdf",
            BYTES_PDF,
            "application/pdf",
            ".pdf",
            "application/pdf",
        ),
        ("documento", "identidade.pdf", BYTES_PDF, "application/pdf", ".pdf", "application/pdf"),
        ("anexo", "comprovante.png", BYTES_PNG, "application/octet-stream", ".png", "image/png"),
    ],
)
def test_upload_aceita_jpg_png_e_pdf_em_todos_os_tipos_e_gera_nome_opaco(
    cliente,
    db,
    tipo,
    nome_original,
    conteudo,
    mime_enviado,
    ext_esperada,
    mime_esperado,
):
    usuario = criar_usuario(db)

    resposta = _enviar(
        cliente,
        usuario,
        tipo=tipo,
        nome=nome_original,
        conteudo=conteudo,
        mime=mime_enviado,
    )

    assert resposta.status_code == 201
    corpo = resposta.json()
    assert corpo["tipo"] == tipo
    assert corpo["mime_type"] == mime_esperado
    assert corpo["tamanho_bytes"] == len(conteudo)
    assert corpo["url"].startswith(f"/api/v1/arquivos/{tipo}/")
    assert corpo["url"].endswith(ext_esperada)
    nome_gerado = corpo["url"].rsplit("/", 1)[-1]
    assert nome_original.split(".")[0].lower() not in nome_gerado.lower()
    assert len(nome_gerado) == 32 + len(ext_esperada)

    leitura = cliente.get(corpo["url"], headers=cabecalho(usuario))
    assert leitura.status_code == 200
    assert leitura.content == conteudo
    assert leitura.headers["content-type"].startswith(mime_esperado)
    assert leitura.headers["x-content-type-options"] == "nosniff"


def test_dois_uploads_do_mesmo_arquivo_geram_urls_distintas(cliente, db):
    usuario = criar_usuario(db)

    primeiro = _enviar(cliente, usuario, nome="foto.jpg").json()["url"]
    segundo = _enviar(cliente, usuario, nome="foto.jpg").json()["url"]

    assert primeiro != segundo


# --- Validação de tipo e tamanho ---------------------------------------------------------


@pytest.mark.parametrize(
    ("nome", "conteudo", "mime"),
    [
        ("script.exe", b"MZ\x90\x00\x03\x00\x00\x00", "application/octet-stream"),
        ("pagina.html", b"<html><body>oi</body></html>", "text/html"),
        ("vetor.svg", b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "image/svg+xml"),
        ("texto.txt", b"apenas texto", "text/plain"),
        ("falso.jpg", b"nao e uma imagem jpeg", "image/jpeg"),
        ("disfarcado.pdf", BYTES_JPEG, "application/pdf"),
        ("extensao_trocada.png", BYTES_JPEG, "image/jpeg"),
    ],
)
def test_arquivo_fora_do_tipo_permitido_e_recusado_com_erro_padrao(
    cliente, db, nome, conteudo, mime
):
    usuario = criar_usuario(db)

    resposta = _enviar(cliente, usuario, nome=nome, conteudo=conteudo, mime=mime)

    assert resposta.status_code == 415
    detalhe = resposta.json()["detail"]
    assert detalhe["codigo"] == "arquivo_tipo_invalido"
    assert "mensagem" in detalhe
    assert set(detalhe["tipos_permitidos"]) == {"jpg", "png", "pdf"}


def test_arquivo_vazio_e_recusado_com_erro_padrao(cliente, db):
    usuario = criar_usuario(db)

    resposta = _enviar(cliente, usuario, nome="vazio.jpg", conteudo=b"", mime="image/jpeg")

    assert resposta.status_code == 400
    assert resposta.json()["detail"]["codigo"] == "arquivo_vazio"


def test_arquivo_acima_de_10_mb_e_recusado_com_erro_padrao(cliente, db):
    usuario = criar_usuario(db)
    excedente = BYTES_JPEG + b"x" * (10 * BYTES_POR_MB - len(BYTES_JPEG) + 1)

    resposta = _enviar(cliente, usuario, nome="grande.jpg", conteudo=excedente, mime="image/jpeg")

    assert resposta.status_code == 413
    detalhe = resposta.json()["detail"]
    assert detalhe["codigo"] == "arquivo_muito_grande"
    assert detalhe["limite_mb"] == 10
    assert "mensagem" in detalhe


def test_arquivo_exatamente_no_limite_e_aceito(cliente, db):
    usuario = criar_usuario(db)
    db.add(Configuracao(chave="anexo_max_mb", perfil="producao", valor="1", descricao="1 MB"))
    db.commit()
    no_limite = BYTES_PNG + b"0" * (1 * BYTES_POR_MB - len(BYTES_PNG))
    acima = no_limite + b"0"

    aceito = _enviar(cliente, usuario, nome="ok.png", conteudo=no_limite, mime="image/png")
    recusado = _enviar(cliente, usuario, nome="grande.png", conteudo=acima, mime="image/png")

    assert aceito.status_code == 201
    assert aceito.json()["tamanho_bytes"] == 1 * BYTES_POR_MB
    assert recusado.status_code == 413
    assert recusado.json()["detail"]["limite_mb"] == 1


# --- Controle de acesso por tipo de arquivo ----------------------------------------------


@pytest.mark.parametrize("tipo_sensivel", ["documento", "selfie"])
def test_documento_e_selfie_so_sao_acessiveis_pelo_dono_e_pelo_admin(
    cliente, db, tipo_sensivel
):
    dono = criar_usuario(db)
    outro = criar_usuario(db)
    admin = criar_usuario(db, papel=PapelUsuario.ADMIN)

    envio = _enviar(
        cliente,
        dono,
        tipo=tipo_sensivel,
        nome="identidade.jpg",
        conteudo=BYTES_JPEG,
        mime="image/jpeg",
    )
    assert envio.status_code == 201
    url = envio.json()["url"]

    sem_token = cliente.get(url)
    assert sem_token.status_code == 401
    assert sem_token.json()["detail"]["codigo"] == "nao_autenticado"

    terceiro = cliente.get(url, headers=cabecalho(outro))
    assert terceiro.status_code == 403
    assert terceiro.json()["detail"]["codigo"] == "sem_acesso_ao_arquivo"

    pelo_dono = cliente.get(url, headers=cabecalho(dono))
    assert pelo_dono.status_code == 200
    assert pelo_dono.content == BYTES_JPEG

    pelo_admin = cliente.get(url, headers=cabecalho(admin))
    assert pelo_admin.status_code == 200
    assert pelo_admin.content == BYTES_JPEG


@pytest.mark.parametrize("tipo_publico", ["foto", "qualificacao", "epi", "anexo"])
def test_demais_tipos_de_arquivo_sao_acessiveis_pela_url_para_o_front(
    cliente, db, tipo_publico
):
    dono = criar_usuario(db)
    outro = criar_usuario(db)

    envio = _enviar(
        cliente,
        dono,
        tipo=tipo_publico,
        nome="item.png",
        conteudo=BYTES_PNG,
        mime="image/png",
    )
    assert envio.status_code == 201
    url = envio.json()["url"]

    assert cliente.get(url).status_code == 200
    assert cliente.get(url, headers=cabecalho(outro)).status_code == 200


def test_arquivo_inexistente_devolve_404_com_erro_padrao(cliente):
    resposta = cliente.get("/api/v1/arquivos/foto/00000000000000000000000000000000.jpg")

    assert resposta.status_code == 404
    assert resposta.json()["detail"]["codigo"] == "arquivo_nao_encontrado"


# --- Interface de armazenamento trocável (preparada para S3) -----------------------------


def test_servico_aceita_implementacao_alternativa_de_armazenamento(db, monkeypatch):
    class ArmazenamentoEmMemoria:
        def __init__(self):
            self.objetos: dict[str, tuple[bytes, str]] = {}

        def salvar(self, chave: str, conteudo: bytes, mime_type: str) -> None:
            self.objetos[chave] = (conteudo, mime_type)

        def ler(self, chave: str) -> bytes | None:
            item = self.objetos.get(chave)
            return item[0] if item else None

        def remover(self, chave: str) -> None:
            self.objetos.pop(chave, None)

    memoria = ArmazenamentoEmMemoria()
    monkeypatch.setattr(arquivos, "obter_armazenamento", lambda: memoria)
    usuario = criar_usuario(db)

    registro = arquivos.salvar_upload(
        db,
        usuario,
        TipoArquivo.EPI,
        BytesIO(BYTES_PNG),
        nome_original="bota.png",
        content_type="image/png",
    )

    assert registro.chave in memoria.objetos
    assert memoria.objetos[registro.chave] == (BYTES_PNG, "image/png")
    assert not Path(settings.uploads_dir).exists()

    lido, conteudo = arquivos.obter_arquivo_para_leitura(db, registro.chave, None)
    assert lido.id == registro.id
    assert conteudo == BYTES_PNG


def test_armazenamento_local_bloqueia_travessia_de_caminho(tmp_path):
    storage = ArmazenamentoLocal(tmp_path / "uploads")

    with pytest.raises(ValueError):
        storage.salvar("../escape.jpg", BYTES_JPEG, "image/jpeg")

    assert storage.ler("../escape.jpg") is None
