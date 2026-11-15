# FreelAI Back

API do FreelAI, plataforma que conecta contratantes a profissionais para serviços pontuais.
Construída com FastAPI, SQLAlchemy, Alembic e PostgreSQL.

## Requisitos

- Python 3.11 ou superior
- Docker (para o PostgreSQL local) ou um PostgreSQL já instalado

## Executar localmente

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env
docker compose up -d db
alembic upgrade head
uvicorn app.main:app --reload
```

A API fica em `http://localhost:8000` e a documentação interativa em
`http://localhost:8000/docs`. A rota `GET /api/v1/health` responde 200 quando tudo está no ar.

Sem Docker, defina `DATABASE_URL=sqlite:///./freelai.db` no `.env` e rode os mesmos comandos
a partir de `alembic upgrade head`.

## Autenticação

Rotas em `/api/v1/auth`:

- `POST /registro`: cria a conta (nasce com estado `pendente`).
- `POST /login`: devolve `access_token` (curto) e `refresh_token` (longo).
- `POST /refresh`: troca um `refresh_token` válido por um novo par de tokens.
- `GET /me`: dados da conta, perfis cadastrados, perfil ativo e se está verificado.
- `PUT /perfil-ativo`: alterna entre `profissional` e `contratante` na mesma conta.

Rotas protegidas recebem `Authorization: Bearer <access_token>`. Dependências disponíveis em
`app/deps.py`: `usuario_atual`, `exige_conta_ativa`, `exige_perfil(TipoPerfil.X)` e `exige_admin`.
Contas suspensas e banidas recebem 403 com `codigo` e, quando houver, `retorno_em`.

As rotas `POST /register`, `POST /login` e `GET /me` da versão anterior continuam disponíveis
até o front migrar para `/api/v1/auth`.

## Perfis de execução

A variável `PERFIL` escolhe o conjunto de prazos usado pela API:

- `producao`: prazos reais (horas e dias).
- `demo`: prazos reduzidos para minutos, usados em apresentações.

## Estrutura

```
app/
  api/v1/      rotas versionadas
  routers/     rotas anteriores à versão 1 (mantidas durante a migração)
  models/      tabelas SQLAlchemy
  config.py    configuração por variáveis de ambiente
  db.py        engine e sessão
alembic/       migrações do banco
tests/         testes automatizados
```

## Migrações

```powershell
alembic upgrade head                              # aplica todas
alembic revision --autogenerate -m "descricao"    # gera nova migração a partir dos modelos
alembic downgrade -1                              # desfaz a última
```

## Testes e lint

```powershell
pytest
ruff check .
```

## Fluxo de branches

O repositório segue Gitflow:

- `main`: versão estável, recebe apenas merges de `develop` e de `hotfix/*`.
- `develop`: integração das funcionalidades em andamento.
- `feature/<descricao>`: nova funcionalidade, criada a partir de `develop` e integrada de volta a ele.
- `release/<versao>` e `hotfix/<descricao>`: preparação de versão e correções urgentes.

Mensagens de commit seguem o padrão `tipo: descrição` (`feat`, `fix`, `refactor`, `test`, `docs`, `chore`).

## Docker

```powershell
docker build -t freelai-back .
docker run --rm -p 8000:8000 --env-file .env freelai-back
```

A imagem aplica as migrações antes de iniciar o servidor.
