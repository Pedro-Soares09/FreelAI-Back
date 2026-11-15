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

## Contrato OpenAPI

O arquivo `contracts/openapi.json` é a ponte entre este repositório e o front. Ele é gerado, nunca
editado à mão:

```powershell
python scripts/export_openapi.py           # regenera o contrato
python scripts/export_openapi.py --check   # falha se estiver desatualizado
```

Toda alteração de rota ou schema deve regenerar e versionar o arquivo no mesmo commit. O front
copia o arquivo e gera seus tipos TypeScript a partir dele.

Rotas provisórias (`app/api/v1/stubs.py`) devolvem dados de exemplo, com o cabeçalho
`X-Stub: true` e sem autenticação, para o front trabalhar antes das rotas reais:

- `GET /api/v1/vagas/{vaga_id}`
- `GET /api/v1/vagas/{vaga_id}/acordo`
- `GET /api/v1/conversas/{conversa_id}`
- `GET /api/v1/mapa/pontos`

O campo `verificado` vem de `GET /api/v1/auth/me`.

## Pagamento

O pagamento é feito somente por Pix e fica retido até a conclusão do serviço. A taxa da
plataforma (`taxa_plataforma_pct`) é descontada do profissional. O gateway Pix e o
reconhecimento facial ficam atrás de interfaces trocáveis em `app/services/pagamentos.py` e
`app/services/biometria.py`; nesta fase ambos são simulados.

Rotas (tag `pagamentos`):

- `POST /api/v1/vagas/{vaga_id}/pagamento`: abre a cobrança Pix (contratante dono da vaga).
- `GET /api/v1/vagas/{vaga_id}/pagamento`: estado, divisão e lançamentos (contratante ou
  profissional contratado).
- `POST /api/v1/pagamentos/{pagamento_id}/simular-confirmacao`: simula a confirmação do Pix.
  Só existe no perfil `demo`; em `producao` responde 404.
- `POST /api/v1/vagas/{vaga_id}/conclusao`: o contratante confirma a conclusão após o fim.
- `POST /api/v1/vagas/{vaga_id}/validacao-facial`: validação facial do contratante; aprovada,
  libera o valor integral ao profissional.

Varreduras registradas pelo módulo: `conclusao_automatica` (sem resposta do contratante em
`confirmacao_conclusao_min`), `liberacao_parcial` (sem validação facial em
`liberacao_parcial_min`, libera `liberacao_parcial_pct` ao profissional e estorna o restante)
e `expiracao_pagamentos` (cobrança não paga em `prazo_pagamento_min` expira e a vaga volta a
`publicada`). Lançamentos nunca são alterados nem removidos: a soma das saídas de um
pagamento encerrado é sempre igual ao valor retido.

## Perfis de execução

A variável `PERFIL` escolhe o conjunto de prazos usado pela API:

- `producao`: prazos reais (horas e dias).
- `demo`: prazos reduzidos para minutos, usados em apresentações.

Todos os prazos, pesos e limites ficam em `app/services/configuracao.py` e são lidos com
`configuracao.get("chave")`. Linhas na tabela `configuracoes` sobrepõem os padrões (use
`semear_configuracoes(db)` para gravá-los no banco).

Rotas administrativas (exigem conta com papel `admin`):

- `POST /api/v1/admin/prazos/varrer`: força a execução imediata das varreduras de prazo
  registradas com `@varredura("nome")` em `app/services/varreduras.py`.
- `GET /api/v1/admin/configuracoes`: valores efetivos do perfil atual.

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
