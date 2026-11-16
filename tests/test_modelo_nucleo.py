from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    Categoria,
    Endereco,
    EstadoConta,
    PerfilContratante,
    PerfilProfissional,
    RiscoCategoria,
    StatusAprovacao,
    TipoPessoa,
    Usuario,
)


def _usuario(db, email="ana@example.com") -> Usuario:
    usuario = Usuario(nome="Ana", email=email, senha_hash="hash")
    db.add(usuario)
    db.flush()
    return usuario


def _perfil_profissional(usuario: Usuario, cpf="12345678909") -> PerfilProfissional:
    return PerfilProfissional(
        usuario_id=usuario.id,
        cpf=cpf,
        data_nascimento=date(1995, 5, 20),
        telefone="81999990000",
    )


def test_conta_nova_comeca_pendente(db):
    usuario = _usuario(db)

    assert usuario.estado == EstadoConta.PENDENTE
    assert usuario.criado_em is not None


def test_mesma_conta_pode_ter_os_dois_perfis(db):
    usuario = _usuario(db)
    db.add(_perfil_profissional(usuario))
    db.add(
        PerfilContratante(
            usuario_id=usuario.id,
            tipo_pessoa=TipoPessoa.FISICA,
            documento="98765432100",
            telefone="81988880000",
        )
    )
    db.commit()
    db.refresh(usuario)

    assert usuario.perfil_profissional is not None
    assert usuario.perfil_contratante is not None


def test_cpf_do_profissional_e_unico(db):
    primeiro = _usuario(db, "a@example.com")
    segundo = _usuario(db, "b@example.com")
    db.add(_perfil_profissional(primeiro))
    db.commit()

    db.add(_perfil_profissional(segundo))
    with pytest.raises(IntegrityError):
        db.commit()


def test_email_do_usuario_e_unico(db):
    _usuario(db, "ana@example.com")
    db.commit()

    db.add(Usuario(nome="Outra", email="ana@example.com", senha_hash="hash"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_profissional_so_e_verificado_apos_aprovacao(db):
    perfil = _perfil_profissional(_usuario(db))
    db.add(perfil)
    db.commit()

    assert perfil.verificado is False
    perfil.verificacao = StatusAprovacao.APROVADO
    assert perfil.verificado is True


def test_perfil_profissional_guarda_localizacao_e_categorias(db):
    usuario = _usuario(db)
    endereco = Endereco(bairro="Várzea", latitude=-8.0505, longitude=-34.9500)
    categoria = Categoria(slug="eletricista", nome="Eletricista", risco=RiscoCategoria.ALTO)
    perfil = _perfil_profissional(usuario)
    perfil.endereco = endereco
    perfil.categorias.append(categoria)
    db.add(perfil)
    db.commit()
    db.refresh(perfil)

    assert perfil.endereco.cidade == "Recife"
    assert perfil.endereco.uf == "PE"
    assert [c.slug for c in perfil.categorias] == ["eletricista"]
    assert perfil.raio_km == 5
