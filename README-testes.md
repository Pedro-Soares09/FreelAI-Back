# Testes do back (FreelAI-Back)

Camada de testes automatizados em Python (pytest). Os testes usam SQLite em memória e não dependem de `demo.db`, `freelai.db`, `uploads/` nem do `.env`.

## Instalar

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

## Rodar

Suíte completa:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Só os testes de autenticação (registro, login, tokens, limites):

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_auth_v1.py tests/test_auth_limites.py -q
```

Verificações do CI (estilo e contrato):

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe scripts/export_openapi.py --check
```

## Cobertura (instrução e ramificação)

```powershell
.\.venv\Scripts\python.exe -m pytest -q --cov=app --cov-branch --cov-report=term-missing
```

Para um módulo específico, por exemplo a autenticação:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_auth_v1.py tests/test_auth_limites.py -q --cov=app.services.autenticacao --cov=app.api.v1.auth --cov-branch --cov-report=term-missing
```

## Identificadores dos casos

Os testes novos de `tests/test_auth_limites.py` citam o ID do caso na docstring (CT-LOG-xx, CT-REG-xx). Os mesmos IDs aparecem nos casos manuais e nos testes de interface do front, o que permite rastrear cada requisito entre as camadas.

## Ambiente para os testes de ponta a ponta (E2E) do front

Os testes E2E ficam no repositório do front (ver `README-testes.md` de lá). Para subir o back com um banco limpo e próprio:

```powershell
.\.venv\Scripts\python.exe scripts/preparar_demo.py --arquivo e2e.db --servir
```

O comando apaga e recria `e2e.db`, aplica as migrações, semeia as contas de demonstração e sobe a API em `http://localhost:8000` com CORS liberado para `http://localhost:3300`. Arquivos `*.db` não são versionados.
