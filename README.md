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
- `GET /api/v1/mapa/pontos`

O campo `verificado` vem de `GET /api/v1/auth/me`.

## Arquivos (upload e leitura)

Módulo único para fotos de perfil, selfies, documentos de identidade, comprovantes de qualificação,
fotos de EPI e anexos de chat. Tipos permitidos: `jpg`, `png` e `pdf`, com limite padrão de 10 MB
(`anexo_max_mb` em `app/services/configuracao.py`). Os arquivos são gravados em `uploads/` com nome
gerado (nunca o original), por trás da interface `ArmazenamentoArquivos` em `app/services/arquivos.py`
(local nesta fase, preparada para troca por S3).

Rotas (tag `arquivos`):

- `POST /api/v1/arquivos`: recebe `tipo` (`foto`, `selfie`, `documento`, `qualificacao`, `epi` ou
  `anexo`) e `arquivo` via `multipart/form-data`, valida formato e tamanho e devolve a URL.
- `GET /api/v1/arquivos/{tipo}/{nome}`: serve o arquivo armazenado. Arquivos de `documento` e
  `selfie` exigem autenticação e só são acessíveis pelo dono ou por conta `admin`.

## Chat e conversas

A conversa nasce somente no aceite do convite (`abrir_conversa_no_aceite`, 1 por vaga) e mantém o
vínculo entre `vaga_id` e `conversa_id`. Fecha para escrita automaticamente quando a vaga cai
(`expirada`, `cancelada`, `falta`, `reembolsada` ou devolvida à publicação) ou é encerrada
financeiramente (`liberada`, `liberada_parcial`).

Como o navegador não envia cabeçalhos no handshake do WebSocket, o token JWT vai na query string
(`?token=<access_token>`) e é validado antes do `accept()`. Falha de autenticação fecha com código
`4401`; usuário que não faz parte da vaga fecha com `4403`; conversa inexistente fecha com `4404`.

Rotas (tag `conversas`):

- `GET /api/v1/conversas`: lista as conversas do usuário com `outra_parte`, `ultima_mensagem` e
  contagem de `nao_lidas`.
- `GET /api/v1/conversas/{conversa_id}`: resumo da conversa com `vaga_id` e estado `aberta`.
- `GET /api/v1/vagas/{vaga_id}/conversa`: ponto de integração que resolve a conversa a partir do
  `vaga_id`.
- `GET /api/v1/conversas/{conversa_id}/mensagens`: histórico de mensagens em ordem cronológica.
- `POST /api/v1/conversas/{conversa_id}/mensagens`: persiste mensagem (`texto` e/ou `anexo_url`) e
  transmite em tempo real aos conectados.
- `POST /api/v1/conversas/{conversa_id}/visto`: marca como lidas as mensagens recebidas da outra
  parte.
- `WS /api/v1/conversas/{conversa_id}/ws?token=<jwt>`: canal em tempo real com broadcast em memória.

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

## Match

`app/services/match.py` escolhe os profissionais convidados para uma vaga:

- `elegiveis(db, vaga)`: conta ativa, perfil verificado, categoria da vaga, distância dentro do
  raio (limitado por `raio_maximo_km`), comprovações exigidas pela categoria (experiência para
  risco médio ou alto, qualificação e EPI), agenda livre e sem convite anterior na vaga.
- `pontuar(db, vaga, profissional)`: nota de 0 a 1 combinando localização, habilidades,
  reputação e preço com os pesos `peso_*` da configuração.
- `selecionar_top3(db, vaga)`: os dois melhores mais o melhor profissional com selo Novo
  (`servicos_para_deixar_de_ser_novo`), ordenados por pontuação.
- `gerar_rodada(db, vaga)`: grava os convites da rodada; recusa com `rodada_em_andamento` (409)
  enquanto houver convite da rodada atual sem resposta.

A distância usa a fórmula de Haversine (`app/services/geo.py`), com uma caixa de latitude e
longitude como pré-filtro no SQL.

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

A API e o banco sobem juntos pelo compose, no perfil `api`:

```powershell
$env:SECRET_KEY = python -c "import secrets; print(secrets.token_urlsafe(48))"
docker compose --profile api up --build
```

A imagem roda com usuário não privilegiado, aplica as migrações antes de iniciar o servidor e
define `AMBIENTE=producao`: sem uma `SECRET_KEY` própria de pelo menos 32 caracteres, a
aplicação se recusa a iniciar. Dentro do contêiner o banco é acessado pelo nome `db`, e não por
`localhost`, por isso o `DATABASE_URL` do `.env.example` (feito para rodar fora do Docker) não deve
ser usado com `docker run`.

## Segurança

- Senhas aceitam no máximo 72 bytes em UTF-8 (limite do bcrypt); o registro recusa valores maiores.
- O token de renovação não serve como token de acesso. Os tokens de renovação não são revogados
  individualmente: uma troca de senha não invalida os já emitidos.
- O SQLite é aberto com chaves estrangeiras ativas, para se comportar como o PostgreSQL.
