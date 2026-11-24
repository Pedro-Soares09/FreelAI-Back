"""Faz o serviço de uma vaga terminar agora, para mostrar conclusão, avaliação e disputa na demo.

Uso (na raiz do repositório, com o mesmo banco da demonstração):

    python scripts/avancar_servico.py 1            # vaga 1, no demo.db
    python scripts/avancar_servico.py 1 --arquivo demo.db

O horário de início da vaga é recuado para que o fim previsto tenha passado há 1 minuto.
No perfil demo as janelas de avaliação e de contestação duram poucos minutos depois do fim,
então rode o comando logo antes de mostrar essas etapas.
Não deve ser executado em ambiente de produção.
"""

import argparse
import os
import sys
from datetime import timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("vaga_id", type=int)
    parser.add_argument("--arquivo", default="demo.db", help="arquivo SQLite da demonstração")
    argumentos = parser.parse_args()

    os.environ["DATABASE_URL"] = f"sqlite:///{(RAIZ / argumentos.arquivo).resolve().as_posix()}"
    os.environ.setdefault("PERFIL", "demo")

    from app.db import SessionLocal
    from app.models import Vaga
    from app.tempo import agora

    with SessionLocal() as db:
        vaga = db.get(Vaga, argumentos.vaga_id)
        if vaga is None:
            print(f"Vaga {argumentos.vaga_id} não encontrada.")
            return 1
        duracao = timedelta(minutes=vaga.duracao_minutos)
        vaga.inicio_em = agora() - duracao - timedelta(minutes=1)
        db.commit()
        print(f"Vaga {vaga.id} ({vaga.titulo}): o serviço terminou há 1 minuto.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
