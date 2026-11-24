# Hospedagem da demonstração

API no Render (contêiner e PostgreSQL gerenciado) e front na Vercel. O arquivo `render.yaml` descreve o serviço e o banco.

## API (Render)

1. No Render, criar um Blueprint apontando para este repositório (branch `main` ou `develop`). Ele cria o serviço `freelai-api` e o banco `freelai-db`.
2. Informar `CORS_ORIGINS` no painel do serviço com o endereço do front, em JSON: `["https://SEU-FRONT.vercel.app"]`.
3. O contêiner migra o banco, semeia a demonstração (`SEMEAR_DEMO=true`) e sobe a API. O endereço de saúde é `/health`.
4. Contas de demonstração e senhas estão em `docs/roteiro-da-demonstracao.md`.

Observações do plano gratuito: o serviço dorme após alguns minutos sem acesso (abrir `/health` antes da apresentação) e a pasta `uploads` é efêmera, então documentos enviados somem a cada novo deploy. O banco gratuito expira depois de um período, então recrie-o antes de uma nova apresentação.

`AMBIENTE=desenvolvimento` é necessário para o semeamento, que é bloqueado em `producao`. O `SECRET_KEY` é gerado pelo Render e continua forte.

## Front (Vercel)

Importar o repositório do front e definir, antes do build (são lidas na compilação):

```
NEXT_PUBLIC_USE_MOCKS=false
NEXT_PUBLIC_MODO_DEMO=true
NEXT_PUBLIC_API_URL=https://SEU-API.onrender.com
NEXT_PUBLIC_APP_URL=https://SEU-FRONT.vercel.app
```

`NEXT_PUBLIC_APP_URL` é o endereço que a página `/qr` transforma em QR Code. Mudou uma variável: refazer o deploy.
