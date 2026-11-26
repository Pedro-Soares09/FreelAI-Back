# Roteiro da demonstração

Jornada completa da FREELAI em cerca de 15 minutos, com duas pessoas apresentando (contratante e profissional) e uma terceira como administração. Cada passo diz quem fala, em qual tela, com qual conta e o que clicar.

Regras de negócio alinhadas na reunião de 17/11 e refletidas aqui: o pagamento do serviço acontece depois da conclusão; horas extras e bônus são combinados entre as partes, com comprovante anexado e taxa de 7% da plataforma; denúncia validada bloqueia só o chat entre as partes e a segunda penalidade bane a conta; o profissional vê um mapa de ofertas próximas filtrado pela sua experiência.

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

   A senha da Maria e da Ana é `senha-segura-123`. A senha da administração é a que o `preparar_demo.py` imprime no resumo (local) ou a variável `SENHA_ADMIN_DEMO` do Render (deploy). As contas `@freelai.local` (senha `FreelAI@2026!`, exceto o admin) trazem dados variados para o painel e a fila, mas não são necessárias para o roteiro.

4. Conferir que `http://localhost:8000/health` responde 200 e que a Maria já vê quatro vagas em "Minhas vagas".

O perfil `demo` encurta as janelas: aceite do convite e pagamento Pix em 2 minutos, avaliação e contestação em 3. Quem apresenta precisa seguir o ritmo abaixo.

## Roteiro

| Tempo | Quem | Tela | O que fazer e falar |
|---|---|---|---|
| 1 min | Apresentador | Abertura | O problema: contratar um serviço pontual rápido, com segurança. A FREELAI encontra os 3 melhores profissionais perto e cuida do acordo, do pagamento e das avaliações. Esta é a versão Beta. |
| 2 min | Profissional novo | `/registro?tipo=profissional` | Criar a conta "Paulo Demo" e preencher o cadastro em 4 etapas (CPF `529.982.247-25`, nascimento 20/05/1994, telefone, bairro Boa Viagem, categorias Montagem e Elétrica, uma experiência). O ponto no mapa vem do bairro. Enviar documento e selfie na verificação e aceitar o consentimento. A tela mostra "em análise". |
| 1 min | Administração | `/admin`, aba Cadastros | Ver a verificação e a experiência na fila, abrir o documento e a selfie e aprovar os dois. O Paulo passa a "verificado" sozinho. Mostrar que Elétrica ainda aparece com "Falta: EPI" no perfil dele (regra de risco). |
| 1 min | Profissional | Mapa de ofertas | Ana abre o mapa de ofertas próximas (`GET /profissional/mapa-ofertas`): só aparecem vagas da categoria e da experiência dela, ordenadas pela distância, com a marcação do que ainda falta aprovar. |
| 1 min | Contratante | `/criar-vaga` | Maria publica "Troca de fiação", categoria Elétrica. O checklist de segurança aparece; tentar publicar sem marcar o item crítico (bloqueia) e depois marcar e publicar. |
| 1 min | Contratante | `/match` | Os 3 melhores profissionais com distância e nota. Escolher Ana Souza. O convite começa a contar 2 minutos. |
| 30 s | Profissional | `/match` (Convites) | Ana vê o convite com a contagem regressiva e aceita. O chat é aberto. |
| 1 min | As duas | `/chat/...` | Trocar duas mensagens. Tentar escrever um telefone: a mensagem é barrada pelo bloqueio de contato. |
| 1 min | As duas | `/acordo/...` | Cada uma dá "Li e aceito". Mostrar valor, taxa de 10% e líquido e a condição de pagamento: Pix depois da conclusão. Com os dois aceites a vaga fica contratada, sem cobrança antecipada. |
| 1 min | Apresentador | Terminal | Rodar `python scripts/avancar_servico.py <id da vaga>` para fazer o serviço terminar agora. Começa a janela de 3 minutos de avaliação e contestação. |
| 1 min | Contratante | `/chat/...` | "Confirmar conclusão do serviço". A cobrança Pix aparece na hora (`/pagamento/...`). |
| 1 min | Contratante | `/pagamento/...` | Copiar o Pix e usar "Simular pagamento". O valor é liberado à Ana, com a taxa de 10% separada no extrato. |
| 1 min | Contratante | Comprovantes da vaga | Anexar um comprovante de bônus de R$ 50 (`POST /vagas/{id}/comprovantes`, tipo `bonus`). A taxa adicional de 7% aparece no resumo e o perfil da Maria ganha o destaque de quem paga adicionais, sem efeito no ranking do match. |
| 1 min | Contratante | `/chat/...` | Avaliar a Ana com 5 estrelas. |
| 1 min | Profissional | `/chat/...` | Avaliar a Maria. Mostrar o botão Denunciar abrindo o formulário (não enviar). |
| 1 min | Administração | `/admin`, aba Denúncias | Mostrar a fila e uma denúncia: ao validar como procedente o chat entre as partes é bloqueado (as demais funções seguem livres) e a conta denunciada soma a primeira penalidade; na segunda, é banida. |
| 30 s | Profissional | `/plano-pro` | Assinar o Plano Pro e mostrar o aviso de que ele não altera o ranking do match. |
| 30 s | Apresentador | Encerramento | Resumo: cadastro verificado, mapa, match, acordo, pagamento depois do serviço, comprovantes, avaliação e moderação, tudo sobre a API real. |

Total: cerca de 15 minutos. Se faltar tempo, cortar o Plano Pro, a mensagem com telefone e o mapa.

### Variação para mostrar a disputa (opcional, +1 minuto)

Depois de rodar `avancar_servico.py`, no lugar de confirmar a conclusão, a Maria usa "Contestar serviço" e escreve o motivo. A vaga vai para "em disputa" e a cobrança fica suspensa. Na aba Disputas a administração decide: reembolso total cancela a cobrança, e reembolso parcial ou pagamento integral reemitem a cobrança com o valor devido.

## Cuidados

- Rodar `avancar_servico.py` só depois do aceite do acordo e logo antes de mostrar conclusão, pagamento, avaliação ou disputa. Passados 3 minutos da conclusão, a avaliação deixa de ser aceita.
- A cobrança Pix vence em 2 minutos. Se vencer, a Maria pede nova cobrança (`POST /vagas/{id}/pagamento`) e segue.
- Se o convite vencer (2 minutos), recriar o estado com `python scripts/preparar_demo.py --servir` e recomeçar do passo da vaga.
- O back precisa estar com o perfil `demo`; o comando acima já define isso. Sem ele, os prazos voltam aos valores de produção.
- Horas extras exigem o texto do acordo prévio; bônus não. Só o contratante anexa adicionais.
- Denúncia procedente bloqueia somente o chat daquela vaga. Duas denúncias procedentes contra a mesma conta a banem de vez; para repetir a demo, recriar o estado inicial.
- Cancelamento de vaga, check-in por GPS e anexos no chat ainda são simulados e ficam fora do roteiro.
- Bairros fora da tabela embutida só são localizados se `GEOCODIFICACAO_OSM=true` (OpenStreetMap, só o nome do bairro vai na consulta); sem isso, informe latitude e longitude.

## Plano B

- Vídeo de reserva gravado na véspera com este mesmo roteiro, em tela cheia.
- Capturas de tela de cada passo na pasta de prints do projeto, para mostrar se a rede ou o back falharem.
- Se o back não subir, mostrar o front em modo de demonstração local: `NEXT_PUBLIC_USE_MOCKS=true npm run dev`, que roda com dados simulados e cobre as telas, mas não a verificação pela administração.

## Recriar o estado inicial

```bash
python scripts/preparar_demo.py --servir
```

O comando apaga o banco `demo.db`, aplica as migrações e semeia categorias com checklist, contas e vagas. Rode de novo sempre que quiser repetir a demonstração do zero.

## Moderação pela administração (opcional, +2 minutos)

A semente cria dados prontos para a conta `admin@example.com`, em `/admin`:

- **Denúncias, Abertas (3):** cobrança fora da plataforma contra Tiago Alves (acima de 48 h úteis), tentativas de enviar telefone (automática) e mensagens ofensivas contra Roberto Cunha. Validar uma delas bloqueia só o chat entre as partes; a segunda penalidade bane a conta.
- **Denúncias, Decididas (5):** quatro procedentes (com chat bloqueado) e uma improcedente.
- **Disputas, Abertas (3):** valores de R$ 260,00 a R$ 480,00, com conversa consultável e as três decisões (pagar o profissional, reembolso parcial, reembolso total).
- **Disputas, Decididas (1):** reembolso parcial de 40%.
- **Banimentos (1):** Rafael Lima, com duas penalidades. O login dele mostra "Conta banida".

Contas extras (senha `senha-segura-123`): `rafael.lima`, `tiago.alves`, `patricia.nunes` (profissionais) e `juliana.prado`, `marcos.teles`, `helena.duarte`, `roberto.cunha` (contratantes), todas com `@example.com`. A Helena Duarte tem o selo de contratante destaque e avaliações, bom exemplo para mostrar o perfil público.

## Perfil do contratante

A Maria edita o perfil em `/perfil` (foto, apresentação, bairro, horário, categorias de interesse, preferências e cor). Os profissionais veem o resultado em `/contratante/<id>`, aberto pelo nome do contratante no card da vaga ou por "Ver perfil" no chat.
