"""Gera o contrato OpenAPI versionado em `contracts/openapi.json`.

Uso:
    python scripts/export_openapi.py           # grava o arquivo
    python scripts/export_openapi.py --check   # falha se o arquivo estiver desatualizado

O arquivo nunca deve ser editado à mão: toda mudança de rota ou schema exige rodar este script
e versionar o resultado. O front gera seus tipos a partir dele.
"""

import argparse
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from app.main import app  # noqa: E402

CAMINHO = RAIZ / "contracts" / "openapi.json"


def gerar() -> str:
    return json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="apenas verifica se está em dia")
    argumentos = parser.parse_args()

    conteudo = gerar()
    if argumentos.check:
        atual = CAMINHO.read_text(encoding="utf-8") if CAMINHO.exists() else ""
        if atual != conteudo:
            print(
                "contracts/openapi.json está desatualizado. Rode: python scripts/export_openapi.py"
            )
            return 1
        print("contracts/openapi.json está em dia.")
        return 0

    CAMINHO.parent.mkdir(parents=True, exist_ok=True)
    CAMINHO.write_text(conteudo, encoding="utf-8", newline="\n")
    print(f"Contrato gravado em {CAMINHO.relative_to(RAIZ)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
