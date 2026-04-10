# Integracoes do Zap Workflow

Este documento resume a configuracao do app em homologacao e producao.

## O que ja esta pronto

- login de usuario
- kanban com estados, departamentos e etiquetas
- conversa vinculada ao card
- identificacao do emissor da mensagem
- envio de mensagens para WhatsApp Business Cloud API
- webhook para receber mensagens do WhatsApp
- agenda compartilhada via Google Sheets
- lembretes configuraveis por minutos antes
- upload de arquivos para anexos no atendimento

## Fluxo de atendimento

```text
WhatsApp -> Webhook -> Ticket novo -> Aguardando -> Departamento correto -> Concluido -> Arquivado em 2 dias
```

## Variaveis de ambiente

As principais variaveis ficam em `.env.example`:

- `SECRET_KEY`
- `DATABASE_URL`
- `BOOTSTRAP_ADMIN_NAME`
- `BOOTSTRAP_ADMIN_EMAIL`
- `BOOTSTRAP_ADMIN_PASSWORD`
- `WHATSAPP_TOKEN`
- `WHATSAPP_PHONE_NUMBER_ID`
- `WHATSAPP_VERIFY_TOKEN`
- `WHATSAPP_API_VERSION`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REDIRECT_URI`
- `UPLOAD_FOLDER`
- `GOOGLE_SERVICE_ACCOUNT_JSON`
- `GOOGLE_SHEETS_SPREADSHEET_ID`
- `GOOGLE_SHEETS_TAB_NAME`
- `GOOGLE_SHEETS_SYNC_ENABLED`
- `GOOGLE_SHEETS_ALLOW_WRITEBACK`
- `REMINDER_MINUTES`
- `REMINDER_SEND_WHATSAPP`
- `TICKET_ARCHIVE_DAYS`

## WhatsApp Business

### 1. O que o sistema faz

- recebe mensagens pelo webhook
- cria ticket novo quando nao existe conversa vinculada
- registra o nome do contato no historico
- grava mensagens de saida com o nome do usuario logado
- permite enviar texto, midia, template, localizacao e contato

### 2. O que configurar na Meta

- `Phone Number ID`
- `Access Token`
- `Verify Token`
- URL publica para webhook

### 3. Webhook

A URL do webhook e:

- `https://seu-dominio.com/webhooks/whatsapp`

No Render, a URL publica e fornecida por `RENDER_EXTERNAL_URL`. Se quiser fixar manualmente, preencha `PUBLIC_BASE_URL`. A callback completa fica:

- `https://seu-app.onrender.com/webhooks/whatsapp`

Se o Zap estiver embutido na Nanotech, use:

- `https://nanotech-lvoz.onrender.com/zap/webhooks/whatsapp`

No ambiente local, o ngrok continua opcional para testes. No Render, nao precisa dele.

Depois use a URL publica no painel da Meta.

### 4. Verificacao

O endpoint `GET /webhooks/whatsapp` valida `hub.verify_token`.

Quando o app roda embutido na Nanotech, a mesma rota fica acessivel em `/zap/webhooks/whatsapp`.

Se estiver correto, o servidor responde com `hub.challenge`.

### 5. Envio de mensagem

O endpoint `POST /api/whatsapp/send` envia:

- texto
- midia
- template
- interativo
- localizacao
- contato
- confirmacao de leitura

## Agenda via Google Sheets

### 1. O que o sistema faz

- escreve os atendimentos com data marcada na planilha
- permite visualizar a pre-visualizacao da aba configurada
- dispara lembretes por WhatsApp no intervalo configurado

### 2. Passo a passo

1. Crie um projeto no Google Cloud.
2. Habilite a Google Sheets API.
3. Crie uma service account.
4. Baixe o JSON da service account.
5. Crie uma planilha compartilhada.
6. Copie o Spreadsheet ID.
7. Compartilhe a planilha com o e-mail da service account.
8. Preencha `GOOGLE_SERVICE_ACCOUNT_JSON`, `GOOGLE_SHEETS_SPREADSHEET_ID` e `GOOGLE_SHEETS_TAB_NAME`.

### 3. Fluxo da agenda

```text
Card com data -> Sync manual ou automatica -> Google Sheets -> Lembrete programado
```

## Banco de dados

O app aceita MySQL ou PostgreSQL via `DATABASE_URL`.

Exemplos:

```env
DATABASE_URL=mysql+pymysql://user:password@host:3306/zap_workflow
DATABASE_URL=postgresql+psycopg2://user:password@host:5432/zap_workflow
```

Se quiser manter o banco local como cache e espelhar os dados em paralelo, defina tambem `BACKUP_DATABASE_URL` com o banco externo.

```env
BACKUP_DATABASE_URL=postgresql+psycopg2://user:password@host:5432/zap_workflow
```

## Teste rapido

### WhatsApp

```bash
curl -X POST http://localhost:5000/api/whatsapp/send \
  -H "Content-Type: application/json" \
  -d '{"to":"5511999999999","body":"Teste de envio"}'
```

### Integracoes

1. Abra `http://localhost:5000/settings`
2. Configure WhatsApp, agenda e lembretes
3. Veja o bloco de status das integracoes
4. Abra `http://localhost:5000/docs` para os fluxos

## Homologacao e producao

1. Rode o app em homologacao com o mesmo banco da versao final.
2. Valide os departamentos, usuarios e estados.
3. Teste a entrada de mensagens reais no WhatsApp.
4. Confira se o ticket novo cai em Aguardando.
5. Confirme se cards concluidos somem apos 2 dias.
6. Publique a revisao no GitHub antes de atualizar producao.


