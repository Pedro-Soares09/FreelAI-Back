# Roteiro da demonstração

Jornada completa da FREELAI em cerca de 14 minutos, com duas pessoas apresentando (contratante e profissional) e uma terceira como administração. Cada passo diz quem fala, em qual tela, com qual conta e o que clicar.

## Antes de começar (10 minutos antes)

1. No repositório do back, recriar o estado inicial e subir a API:

   ```bash
   python scripts/preparar_demo.py --servir
   ```

2. No repositório do front, com um build de produção em modo real:

   ```bash
   NEXT_PUBLIC_USE_MOCKS=false NEXT_PUBLIC_MODO_DEMO=true NEXT_PUBLIC_API_URL=http://localhost:8000 npm run build
   npm start -- -p 3300
   ```

3. Abrir três janelas anônimas do navegador, uma para cada papel, em `http://localhost:3300/login`:
   - Contratante: `maria@example.com`
   - Profissional: `ana@example.com`
   - Administração: `admin@example.com`

   A senha de todas é `senha-segura-123`. As contas `@freelai.local` (senha `FreelAI@2026!`) trazem dados variados para o painel e a fila, mas não são necessárias para o roteiro.

4. Conferir que `http://localhost:8000/health` responde 200 e que a Maria já vê quatro vagas em "Minhas vagas".

O perfil `demo` encurta as janelas: aceite do convite e pagamento Pix em 2 minutos, avaliação e contestação em 3. Quem apresenta precisa seguir o ritmo abaixo.

## Roteiro

| Tempo | Quem | Tela | O que fazer e falar |
|---|---|---|---|
| 1 min | Apresentador | Abertura | O problema: contratar um serviço pontual rápido, com segurança. A FREELAI encontra os 3 melhores profissionais perto e cuida do acordo e do pagamento. |
| 2 min | Profissional novo | `/registro?tipo=profissional` | Criar a conta "Paulo Demo" e preencher o cadastro em 4 etapas (CPF `529.982.247-25`, nascimento 20/05/1994, telefone, bairro Boa Viagem, categorias Montagem e Elétrica, uma experiência). Enviar documento e selfie na verificação e aceitar o consentimento. A tela mostra "em análise". |
| 1 min | Administração | `/admin`, aba Cadastros | Ver a verificação e a experiência na fila, abrir o documento e a selfie e aprovar os dois. O Paulo passa a "verificado" sozinho. Mostrar que Elétrica ainda aparece com "Falta: EPI" no perfil dele (regra de risco). |
| 1 min | Contratante | `/criar-vaga` | Maria publica "Troca de fiação", categoria Elétrica. O checklist de segurança aparece; tentar publicar sem marcar o item crítico (bloqueia) e depois marcar e publicar. |
| 1 min | Contratante | `/match` | Os 3 melhores profissionais com distância e nota. Escolher Ana Souza. O convite começa a contar 2 minutos. |
| 30 s | Profissional | `/match` (Convites) | Ana vê o convite com a contagem regressiva e aceita. O chat é aberto. |
| 1 min | As duas | `/chat/...` | Trocar duas mensagens. Tentar escrever um telefone: a mensagem é barrada pelo bloqueio de contato. |
| 1 min | As duas | `/acordo/...` | Cada uma dá "Li e aceito". Mostrar valor, taxa de 10% e líquido. |
| 1 min | Contratante | `/pagamento/...` | Copiar o Pix e usar "Simular pagamento". O valor fica retido. |
| 1 min | Apresentador | Terminal | Rodar `python scripts/avancar_servico.py <id da vaga>` para fazer o serviço terminar agora. Começa a janela de 3 minutos de avaliação e contestação. |
| 1 min | Contratante | `/chat/...` | "Confirmar conclusão do serviço". Avaliar a Ana com 5 estrelas. |
| 1 min | Profissional | `/chat/...` | Avaliar a Maria. Mostrar o botão Denunciar abrindo o formulário (não enviar). |
| 30 s | Profissional | `/plano-pro` | Assinar o Plano Pro e mostrar o aviso de que ele não altera o ranking do match. |
| 30 s | Apresentador | Encerramento | Resumo: cadastro verificado, match, acordo, pagamento retido, conclusão e avaliação, tudo sobre a API real. |

Total: cerca de 14 minutos. Se faltar tempo, cortar o Plano Pro e a mensagem com telefone.

### Variação para mostrar a disputa (opcional, +1 minuto)

Depois de rodar `avancar_servico.py`, no lugar de avaliar, a Maria usa "Contestar serviço" e escreve o motivo. A vaga vai para "em disputa" e o pagamento fica travado até a decisão da administração (a decisão pela tela do admin ainda não está pronta; mostrar apenas o estado).

## Cuidados

- Rodar `avancar_servico.py` só depois do pagamento retido e logo antes de mostrar conclusão, avaliação ou disputa. Passados 3 minutos da conclusão, a avaliação deixa de ser aceita.
- Se o convite ou o Pix vencerem (2 minutos), recriar o estado com `python scripts/preparar_demo.py --servir` e recomeçar do passo da vaga.
- O back precisa estar com o perfil `demo`; o comando acima já define isso. Sem ele, os prazos voltam aos valores de produção.
- As abas Denúncias e Disputas da administração ainda estão vazias na tela; não prometer essa parte.
- Cancelamento de vaga, check-in por GPS e anexos no chat ainda são simulados e ficam fora do roteiro.

## Plano B

- Vídeo de reserva gravado na véspera com este mesmo roteiro, em tela cheia.
- Capturas de tela de cada passo na pasta de prints do projeto, para mostrar se a rede ou o back falharem.
- Se o back não subir, mostrar o front em modo de demonstração local: `NEXT_PUBLIC_USE_MOCKS=true npm run dev`, que roda com dados simulados e cobre as telas, mas não a verificação pela administração.

## Recriar o estado inicial

```bash
python scripts/preparar_demo.py --servir
```

O comando apaga o banco `demo.db`, aplica as migrações e semeia categorias com checklist, contas e vagas. Rode de novo sempre que quiser repetir a demonstração do zero.
