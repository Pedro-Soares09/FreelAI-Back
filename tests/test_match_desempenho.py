"""O custo em consultas do match não pode crescer com o número de candidatos."""

from sqlalchemy import event

from app.services import match
from tests.fabricas import criar_categoria, criar_profissional, criar_vaga


def _contar_consultas(db, funcao) -> int:
    contador = {"n": 0}

    def _contar(*_args):
        contador["n"] += 1

    motor = db.get_bind()
    event.listen(motor, "before_cursor_execute", _contar)
    try:
        funcao()
    finally:
        event.remove(motor, "before_cursor_execute", _contar)
    return contador["n"]


def _cenario(db, quantidade: int):
    categoria = criar_categoria(db)
    for indice in range(quantidade):
        criar_profissional(
            db,
            categorias=(categoria,),
            latitude=-8.0505 + indice * 0.001,
            servicos_concluidos=indice,
        )
    vaga = criar_vaga(db, categoria=categoria)
    db.expire_all()
    return vaga


def test_selecao_faz_o_mesmo_numero_de_consultas_com_poucos_ou_muitos_candidatos(
    engine,
):
    from sqlalchemy.orm import sessionmaker

    fabrica = sessionmaker(bind=engine)

    pequeno = fabrica()
    vaga_pequena = _cenario(pequeno, 3)
    consultas_poucos = _contar_consultas(
        pequeno, lambda: match.selecionar_top3(pequeno, vaga_pequena)
    )
    pequeno.close()

    grande = fabrica()
    vaga_grande = _cenario(grande, 25)
    consultas_muitos = _contar_consultas(grande, lambda: match.selecionar_top3(grande, vaga_grande))
    resultado = match.selecionar_top3(grande, vaga_grande)
    grande.close()

    assert len(resultado) == 3
    # Os 25 candidatos novos e antigos entram na mesma leitura em lote.
    assert consultas_muitos <= consultas_poucos + 1
    assert consultas_muitos <= 20


def test_pontuar_um_profissional_isolado_continua_funcionando(db):
    categoria = criar_categoria(db)
    profissional = criar_profissional(db, categorias=(categoria,))
    vaga = criar_vaga(db, categoria=categoria)

    nota = match.pontuar(db, vaga, profissional)

    assert 0.0 <= nota <= 1.0
