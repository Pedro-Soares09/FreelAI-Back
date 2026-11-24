"""Recria o estado inicial da demonstração em um comando.

Uso (na raiz do repositório, com o ambiente virtual ativo):

    python scripts/preparar_demo.py            # recria o banco local e semeia
    python scripts/preparar_demo.py --servir   # idem e sobe a API em http://localhost:8000

O banco é um arquivo SQLite (`demo.db` por padrão, ou o caminho em `--arquivo`), apagado e
recriado a cada execução. A API sobe com o perfil `demo` (janelas de aceite e de pagamento de
poucos minutos) e libera CORS para o front em http://localhost:3300.
Não deve ser executado em ambiente de produção.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
PORTA_FRONT = "http://localhost:3300"


def _executar(comando: list[str], ambiente: dict[str, str]) -> None:
    subprocess.run(comando, cwd=RAIZ, env=ambiente, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--arquivo", default="demo.db", help="arquivo SQLite da demonstração")
    parser.add_argument("--porta", default="8000", help="porta da API")
    parser.add_argument("--servir", action="store_true", help="sobe a API depois de semear")
    argumentos = parser.parse_args()

    arquivo = (RAIZ / argumentos.arquivo).resolve()
    ambiente = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{arquivo.as_posix()}",
        "PERFIL": "demo",
        "CORS_ORIGINS": f'["{PORTA_FRONT}"]',
    }

    if arquivo.exists():
        arquivo.unlink()
    _executar([sys.executable, "-m", "alembic", "upgrade", "head"], ambiente)
    _executar([sys.executable, "scripts/semear_demo.py"], ambiente)

    print(f"\nBanco da demonstração pronto em {arquivo}")
    print("Contas (senha senha-segura-123): maria@example.com (contratante),")
    print("ana@example.com e carlos, bruna, diego, elisa @example.com (profissionais),")
    print("admin@example.com (admin).")

    if argumentos.servir:
        _executar(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", argumentos.porta],
            ambiente,
        )
    else:
        print(
            "\nPara subir a API com este banco, rode o mesmo comando com --servir.\n"
            "Front: NEXT_PUBLIC_USE_MOCKS=false NEXT_PUBLIC_MODO_DEMO=true "
            f"NEXT_PUBLIC_API_URL=http://localhost:{argumentos.porta}\n"
            "(build de produção e npm start -p 3300)."
        )


if __name__ == "__main__":
    main()
