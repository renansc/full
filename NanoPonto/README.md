# NanoPonto

Aplicacao web REP-P em base Flask para registro de ponto com:

- marcacao por celular, browser e RFID/dispositivo eletronico
- hora oficial consultada via NTP dos servidores publicos do Observatorio Nacional
- registro imutavel com NSR sequencial e hash SHA-256 por batida
- logs completos de auditoria
- geracao de AFD no leiaute oficial `003`
- exportacao pronta para fiscalizacao
- comprovante em PDF, portal web e envio por e-mail
- modulo RH para banco de horas, feriados, ferias e ajustes

## Importante

Esta entrega prepara a aplicacao para **homologacao**. Ela nao declara homologacao final automatica por si so.

Pontos que dependem de etapa externa formal:

- certificado de registro do programa no INPI para o REP-P, conforme art. 91 da Portaria 671/2021
- certificados ICP-Brasil para assinatura qualificada do AFD, comprovantes PDF e atestado tecnico
- assinatura formal do responsavel legal e do responsavel tecnico no Atestado Tecnico e Termo de Responsabilidade
- validacao juridico-trabalhista final antes da entrada em producao

## Executar localmente

1. Crie um ambiente virtual Python.
2. Instale as dependencias:
   - `pip install -r requirements.txt`
3. Rode a aplicacao:
   - `python app.py`
4. Abra:
   - `http://localhost:5000`

## Deploy com Docker

Arquivos incluidos:

- `Dockerfile`
- `docker-compose.yml`
- `.dockerignore`
- `.env.example`
- `gunicorn.conf.py`

### Build manual

```bash
docker build -t nanoponto .
docker run --rm -p 5000:5000 \
  -e SECRET_KEY=troque-esta-chave \
  -e DEFAULT_TIMEZONE=America/Sao_Paulo \
  -e ALLOW_SYSTEM_TIME_FALLBACK=1 \
  -e DATABASE_URL=sqlite:////app/data/nanoponto.db \
  -v $(pwd)/data:/app/data \
  nanoponto
```

### Docker Compose

1. Copie `.env.example` para `.env` e ajuste os valores.
2. Suba o container:
   - `docker compose up -d --build`
3. Verifique a saude:
   - `http://localhost:5000/health`

Observacao:

- Com `sqlite`, mantenha o volume `./data:/app/data`.
- Em producao, o recomendado e usar MySQL gerenciado via `MYSQL_*` ou `DATABASE_URL`.

## Deploy no Render

O projeto ja inclui `render.yaml` para deploy como Web Service.

### Opcao 1: Blueprint

1. Suba o repositorio no GitHub.
2. No Render, escolha `New +` > `Blueprint`.
3. Selecione o repositorio.
4. Confirme o `render.yaml`.

### Opcao 2: Web Service manual

Use estes valores:

- Root Directory: `NanoPonto`
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn -c gunicorn.conf.py app:app`
- Health Check Path: `/health`

Variaveis importantes:

- `SECRET_KEY`
- `DEFAULT_TIMEZONE=America/Sao_Paulo`
- `ALLOW_SYSTEM_TIME_FALLBACK=1`
- `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD`
- `DATABASE_URL` opcional, se quiser sobrescrever a string de conexao

Se o banco for MySQL, o app monta a string automaticamente a partir das variaveis `MYSQL_*`.

Observacao:

- O `render.yaml` foi preparado para banco externo MySQL.
- Configure as variaveis `MYSQL_*` no painel do Render ou sobrescreva `DATABASE_URL`.

## Variaveis de ambiente

- `DATABASE_URL`: opcional. Se vazio, o app usa `MYSQL_*` quando existir ou SQLite local como fallback
- `MYSQL_HOST`
- `MYSQL_PORT`
- `MYSQL_DATABASE`
- `MYSQL_USER`
- `MYSQL_PASSWORD`
- `SECRET_KEY`
- `DEFAULT_TIMEZONE`
- `ALLOW_SYSTEM_TIME_FALLBACK=1`: permite fallback para o relogio do servidor se o NTP do ON estiver indisponivel
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASSWORD`
- `SMTP_FROM`

## Rotas principais

- `GET /health`
- `GET /api/auth/me`
- `GET /api/bootstrap`
- `GET /api/integrations/email/status`
- `POST /api/punches`
- `POST /api/integrations/email/test`
- `GET /api/afd.txt`
- `GET /api/fiscalizacao.zip`
- `GET /api/receipts/<id>.pdf`

## Documentacao

- [Conformidade Portaria 671/2021](docs/PORTARIA_671_CONFORMIDADE.md)
- [Homologacao e publicacao](docs/HOMOLOGACAO_E_PUBLICACAO.md)
- [Atestado tecnico e termo de responsabilidade](docs/ATESTADO_TECNICO_E_TERMO_DE_RESPONSABILIDADE.md)
