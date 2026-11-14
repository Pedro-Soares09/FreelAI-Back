# FreelAI Back

API inicial do FreelAI com autenticação, banco SQLite e endpoints de dados mock.

## Requisitos

- Python 3.11 ou superior
- pip

## Executar localmente

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

A API fica disponível em `http://localhost:8000` e a documentação interativa em
`http://localhost:8000/docs`. O banco SQLite `freelai.db` é criado automaticamente.

## Autenticação

- `POST /register`: cria um usuário com `nome`, `email`, `senha` e `tipo` opcional.
- `POST /login`: recebe `email` e `senha`, retornando um token Bearer.
- `GET /me`: retorna os dados do usuário autenticado.

Exemplo de registro:

```json
{
  "nome": "Ana",
  "email": "ana@example.com",
  "senha": "senha-segura-123",
  "tipo": "freelancer"
}
```

Configure `SECRET_KEY` com um valor aleatório e privado antes de publicar o serviço.
As demais configurações estão documentadas em `.env.example`.

## Endpoints mock

- `GET /jobs`
- `GET /conversations`
- `GET /payments`

As respostas são carregadas dos arquivos JSON em `app/mocks/`.

## Testes

```powershell
pytest
```

## Docker

```powershell
docker build -t freelai-back .
docker run --rm -p 8000:8000 --env-file .env freelai-back
```
