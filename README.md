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

Como o navegador não envia cabeçalhos no handshake do WebSocket, há duas formas de autenticar:

1. **Query string (`?token=<access_token>`, padrão `auth=query`)**: validado antes do `accept()`,
   fechando imediatamente com `4401` em caso de token ausente ou inválido. **Atenção:** tokens na
   query string podem ser registrados em logs de acesso/proxy; configure redação do parâmetro
   `token` no proxy ou prefira o modo abaixo.
2. **Primeira mensagem (`?auth=mensagem`, recomendada para evitar JWT em logs)**: aceita o socket,
   aguarda até 5s pela primeira mensagem JSON `{"tipo": "auth", "token": "<jwt>"}`, responde
   `{"tipo": "autenticado", "conversa_id": ..., "usuario_id": ...}` e fecha com `4401` se falhar.

Clientes fora do navegador também podem enviar o cabeçalho `Authorization: Bearer <jwt>`.
Em qualquer modo, usuário fora da vaga fecha com `4403` e conversa inexistente fecha com `4404`.
O endpoint WebSocket abre uma sessão curta de banco apenas durante a autenticação e no
processamento de cada mensagem, sem manter conexões do pool presas enquanto o socket está ocioso.

Rotas (tag `conversas`):

- `GET /api/v1/conversas`: lista as conversas do usuário com `outra_parte`, `ultima_mensagem` e
  contagem de `nao_lidas`.
- `GET /api/v1/conversas/{conversa_id}`: resumo da conversa com `vaga_id` e estado `aberta`.
- `GET /api/v1/vagas/{vaga_id}/conversa`: ponto de integração que resolve a conversa a partir do
  `vaga_id`.
- `GET /api/v1/conversas/{conversa_id}/mensagens`: histórico de mensagens em ordem cronológica.
- `POST /api/v1/conversas/{conversa_id}/mensagens`: persiste mensagem (`texto` e/ou `anexo_url`) e
  transmite em tempo real aos conectados.
- `POST /api/v1/conversas/{conversa_id}/anexos`: envia anexo JPG/PNG/PDF até `anexo_max_mb` MB
  (`multipart/form-data`, campo `arquivo` e `texto` opcional) e transmite aos conectados.
- `GET /api/v1/conversas/{conversa_id}/anexos/{nome}`: entrega o anexo restrito aos participantes.
- `POST /api/v1/conversas/{conversa_id}/visto`: marca como lidas as mensagens recebidas da outra
  parte.
- `WS /api/v1/conversas/{conversa_id}/ws?token=<jwt>` (ou `?auth=mensagem`): canal em tempo real
  com broadcast em memória.

Mensagens no REST, WebSocket e legendas de anexos passam pelo bloqueio de telefone brasileiro e
referências ao WhatsApp (`422 contato_externo_bloqueado` no REST ou evento `erro` ao remetente no
WebSocket). Cada tentativa bloqueada é persistida na tabela `tentativas_contato` por
`(conversa_id, usuario_id)` e abre uma denúncia automática a partir da 3ª tentativa.

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
- `GET /api/v1/admin/disputas` e `GET /api/v1/admin/disputas/{disputa_id}`: aba de disputas com o
  histórico do chat e os anexos de cada vaga.
- `POST /api/v1/admin/disputas/{disputa_id}/decisao`: decide a disputa (`reembolso_total`,
  `reembolso_parcial` ou `pagamento_integral`) e gera os lançamentos no livro do pagamento.

## Disputas (contestação em 24h e decisão do admin)

O contratante dono da vaga pode contestar o serviço dentro das **24 horas** de confirmação
(`confirmacao_conclusao_min`) após o fim previsto do serviço, enquanto o pagamento estiver `retido`
(`app/services/disputas.py`). A abertura trava a vaga em `em_disputa`, impedindo que a varredura de
72h (`liberacao_parcial`) libere o valor automaticamente.

O administrador decide em até 48h úteis usando exclusivamente as funções do livro de lançamentos
(`app/services/pagamentos.py`):

- `reembolso_total` → `estornar_integral`: devolve 100% ao contratante, sem taxa, e muda a vaga para
  `reembolsada`.
- `reembolso_parcial` → `liberar_parcial(100 - percentual_reembolso)`: libera a parcela ao
  profissional (descontada a taxa da plataforma) e estorna o restante ao contratante (`liberada_parcial`).
- `pagamento_integral` → `liberar_integral`: libera 100% ao profissional (descontada a taxa) e muda
  a vaga para `liberada`.

Rotas (tag `disputas` e `admin`):

- `POST /api/v1/vagas/{vaga_id}/disputa`: abre a contestação e trava a vaga em `em_disputa`.
- `GET /api/v1/vagas/{vaga_id}/disputa`: estado da disputa e lançamentos (contratante ou profissional).
- `GET /api/v1/admin/disputas`: lista as disputas com `chat`, `anexos`, `pagamento` e tempo em espera.
- `POST /api/v1/admin/disputas/{disputa_id}/decisao`: decide a disputa com motivo obrigatório.
- `GET /api/v1/admin/denuncias`: fila de denúncias (manuais e automáticas) com tempo em espera e
  meta de 48h úteis.
- `POST /api/v1/admin/denuncias/{denuncia_id}/decisao`: decide a denúncia como `procedente` ou
  `improcedente` com motivo.

## Denúncias e fila administrativa (SEG05)

Qualquer usuário autenticado pode registrar uma denúncia a partir de um perfil (`denunciado_id`),
de uma vaga (`vaga_id`) ou de uma conversa de chat (`conversa_id`), informando `motivo` e
`descricao`. Denúncias automáticas do chat (`origem = automatica`) entram na mesma fila.

- `POST /api/v1/denuncias`: abre uma denúncia na fila administrativa.
- `GET /api/v1/denuncias` e `GET /api/v1/denuncias/{denuncia_id}`: consulta sem jamais expor
  `denunciante_id` ao denunciado.
- `GET /api/v1/admin/denuncias`: lista a fila com `tempo_espera_minutos`,
  `tempo_espera_horas_uteis`, `meta_horas_uteis` (48h úteis) e `acima_da_meta`.
- `POST /api/v1/admin/denuncias/{denuncia_id}/decisao`: grava `procedente` ou `improcedente` com
  `decisao_motivo` e `decidido_em`. Denúncias procedentes contam para a reputação e suspendem a
  conta ao atingir `denuncias_para_suspensao`.

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

## Notificações no app (central, Pro imediato e atraso de 2 min)

A central de notificações (`app/services/notificacoes.py`) entrega alertas por polling REST (sem
e-mail e sem WebSocket no MVP). Cada notificação é gravada com `visivel_em`:

- **Plano Pro**: `visivel_em = agora()` (visibilidade imediata).
- **Demais contas**: `visivel_em = agora() + notificacao_atraso_min` (2 minutos em `producao` e 0
  no perfil `demo`).

Eventos emitidos: `selecionado_top3`, `convite_escolhido`, `convite_aceito`, `contrato_pronto`,
`pagamento_retido`, `lembrete_confirmacao` (varredura `lembretes_confirmacao`),
`pagamento_liberado`, `avaliacao_pendente` e `denuncia_decidida`.

Rotas (tag `notificacoes`):

- `GET /api/v1/notificacoes`: lista as notificações cujo `visivel_em <= agora()` e informa
  `nao_lidas`.
- `GET /api/v1/notificacoes/nao-lidas`: contagem rápida de notificações visíveis não lidas.
- `POST /api/v1/notificacoes/{notificacao_id}/lida`: marca uma notificação visível como lida.
- `POST /api/v1/notificacoes/ler-todas`: marca todas as notificações visíveis como lidas.
## Avaliação mútua, reputação 70/20/10 e selo Novo

Após a conclusão do serviço (`concluida`, `liberada` ou `liberada_parcial`), contratante e
profissional têm **48 horas** (`avaliacao_prazo_min`) para avaliar a outra parte com nota de 1 a 5
e comentário (`app/services/avaliacoes.py`). A avaliação enviada por um lado permanece oculta
(`visivel_em = null`) até que o outro lado também avalie ou o prazo de 48h encerre (varredura
`revelacao_avaliacoes`).

O selo **Novo** permanece ativo até o **5º serviço concluído** (`servicos_concluidos <= 5`) e
desaparece ao concluir o **6º serviço**.

Rotas (tag `avaliacoes`):

- `POST /api/v1/vagas/{vaga_id}/avaliacoes`: registra a avaliação (1 a 5 e comentário opcional).
- `GET /api/v1/vagas/{vaga_id}/avaliacoes`: estado das avaliações da vaga (oculta a nota da outra
  parte até ambos avaliarem ou o prazo acabar).
- `GET /api/v1/usuarios/{usuario_id}/avaliacoes`: avaliações já reveladas recebidas pelo usuário.
- `GET /api/v1/usuarios/{usuario_id}/reputacao`: reputação 70/20/10 detalhada e selo Novo.

### Fórmula da reputação (70 / 20 / 10)

```text
Reputação = 0,70 × Média
          + 0,20 × [ 5 × (1 - taxa_cancelamento_tardio_nos_últimos_20) ]
          + 0,10 × max(0, 5 - 2 × denúncias_procedentes)
```

- **Média (70%)**: média das avaliações visíveis (1 a 5); quem ainda não possui avaliação visível
  começa com média neutra **4,0**.
- **Cancelamentos (20%)**: `5 × (1 - taxa)`, onde `taxa` é a proporção de cancelamentos tardios nos
  últimos 20 compromissos.
- **Denúncias (10%)**: começa em `5,0` e perde `2,0` pontos por denúncia procedente (mínimo `0,0`).

Exemplos verificados pelos testes automatizados (`tests/test_avaliacoes.py`):

| Cenário | Média (70%) | Cancelamentos tardios nos últimos 20 (20%) | Denúncias procedentes (10%) | Cálculo | Reputação final |
| :--- | :---: | :---: | :---: | :--- | :---: |
| **1. Conta nova (neutra)** | Sem avaliações → `4,0` | `0 / 20` (`taxa = 0%`) → `5,0` | `0` → `5,0` | `0,70×4,0 + 0,20×5,0 + 0,10×5,0` | **4,30** |
| **2. Profissional exemplar** | Média `5,0` | `0 / 20` (`taxa = 0%`) → `5,0` | `0` → `5,0` | `0,70×5,0 + 0,20×5,0 + 0,10×5,0` | **5,00** |
| **3. Com 1 cancelamento tardio e 1 denúncia** | Média `4,5` | `1 / 20` (`taxa = 5%`) → `4,75` | `1` → `3,0` | `0,70×4,5 + 0,20×4,75 + 0,10×3,0` | **4,40** |
| **4. Reincidente (5 cancelamentos e 3 denúncias)** | Média `3,0` | `5 / 20` (`taxa = 25%`) → `3,75` | `3` → `max(0, 5-6) = 0,0` | `0,70×3,0 + 0,20×3,75 + 0,10×0,0` | **2,85** |

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

## Dados de demonstração (US10)

O FreelAI disponibiliza um comando para popular o banco de dados com informações fictícias, utilizadas durante o desenvolvimento, testes e apresentações do projeto.

### Como executar

Antes de executar o seed, certifique-se de que as migrações foram aplicadas:

```bash
alembic upgrade head
```

Em seguida, execute:

```bash
python -m app.seed
```

O comando pode ser executado novamente sem duplicar os registros existentes (operação idempotente).

### Dados criados

O seed disponibiliza:

- 5 categorias de serviços: montagem de móveis, limpeza, elétrica, pintura e jardinagem.
- Categorias classificadas por nível de risco.
- Equipamentos de proteção individual (EPI) por categoria.
- Checklists de segurança por categoria.
- 1 conta administrativa.
- 20 profissionais fictícios distribuídos em bairros do Recife.
- 5 contratantes fictícios.
- 6 vagas em diferentes estados.

Os profissionais possuem diferentes situações de cadastro e verificação, incluindo profissionais novos e assinantes Pro em cada categoria principal.

Também são utilizadas coordenadas geográficas fictícias para representar profissionais dentro e fora do raio de 5 km das vagas.

### Estado inicial da demonstração em um comando

```bash
python scripts/preparar_demo.py            # recria o banco local (demo.db) e semeia
python scripts/preparar_demo.py --servir   # idem e sobe a API com o perfil demo na porta 8000
```

Usa o `scripts/semear_demo.py` (contas e vagas da jornada integrada). O banco é apagado e
recriado a cada execução, o que também serve para repetir a demonstração depois que as janelas
de aceite e de pagamento (minutos no perfil `demo`) vencerem.

O passo a passo da apresentação, com contas, tempos e cuidados, está em
`docs/roteiro-da-demonstracao.md`. Para fazer o serviço de uma vaga terminar agora (e mostrar
conclusão, avaliação e disputa dentro das janelas curtas do perfil `demo`), use
`python scripts/avancar_servico.py <id da vaga>`.

### Credenciais de demonstração

As contas de profissionais e contratantes criadas pelo seed usam a mesma senha:

**Senha:** `FreelAI@2026!`

**Administrador:** `admin@freelai.local`. A senha vem da variável de ambiente `SENHA_ADMIN_DEMO`
(sem ela, a conta nasce com senha aleatória e inacessível). No ambiente local, o comando
`python scripts/preparar_demo.py` define a variável só para o seu processo e imprime o valor.

**Profissionais:**
- `profissional01@freelai.local` até `profissional20@freelai.local`

**Contratantes:**
- `contratante01@freelai.local` até `contratante05@freelai.local`

### Observações

- Todos os dados são fictícios.
- O seed é destinado exclusivamente aos ambientes de desenvolvimento, testes e demonstração.
- Não utilizar essas credenciais em ambiente de produção.
