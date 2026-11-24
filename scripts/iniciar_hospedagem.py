"""Ponto de entrada da hospedagem (Render): migra o banco, semeia a demonstração e sobe a API.

Variáveis lidas:
    PORT         porta da API (padrão 8000)
    SEMEAR_DEMO  "true" para criar categorias, contas e vagas de demonstração (idempotente)

O semeamento só roda com AMBIENTE diferente de "producao", porque os dados são fictícios.
"""

import os
import subprocess
import sys


def _executar(comando: list[str]) -> None:
    subprocess.run(comando, check=True)


def main() -> None:
    _executar([sys.executable, "-m", "alembic", "upgrade", "head"])
    if os.environ.get("SEMEAR_DEMO", "").lower() == "true":
        _executar([sys.executable, "-m", "app.seed"])
        _executar([sys.executable, "scripts/semear_demo.py"])
    porta = os.environ.get("PORT", "8000")
    comando = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        porta,
    ]
    if os.name == "nt":  # o execvp do Windows não preserva caminhos com espaço
        _executar(comando)
    else:
        os.execvp(sys.executable, comando)


if __name__ == "__main__":
    main()
