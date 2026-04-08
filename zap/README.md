# Zap Workflow

Sistema web em Flask para vendas e prestacao de servicos com:

- login por usuario
- kanban operacional
- departamentos para distribuir atendimento
- cadastro rapido de cliente, servico e conversa
- estados e etiquetas customizaveis
- mensagem de WhatsApp identificando cliente e operador
- agenda compartilhada via Google Sheets
- lembretes programados
- suporte a MySQL ou PostgreSQL via `DATABASE_URL`
- deploy com `gunicorn`

## Estrutura

- `app/` contem a aplicacao Flask
- `app/templates/` contem as telas HTML
- `app/static/` contem CSS e JavaScript
- `docs/` contem a documentacao de integracoes
- `.env.example` mostra as variaveis de ambiente

## Configuracao passo a passo

1. Copie `.env.example` para `.env`.
2. Escolha um banco em `DATABASE_URL`.
3. Defina o usuario admin inicial.
4. Configure WhatsApp, Google Sheets e lembretes.
5. No Render, o app usa `RENDER_EXTERNAL_URL` automaticamente. Se quiser, voce ainda pode definir `PUBLIC_BASE_URL` manualmente.
6. Inicialize o banco com `init-db`.
7. Abra o sistema e revise o menu Configuracao.

## Rodar localmente

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -m flask --app wsgi:app init-db
python -m flask --app wsgi:app run
```

## Rodar com Docker

```bash
docker compose up --build
```

Depois acesse:

- `http://localhost:5000/login`

## Banco de dados

O app funciona com:

- MySQL
- PostgreSQL
- SQLite para testes locais simples

Exemplos de `DATABASE_URL`:

```env
DATABASE_URL=mysql+pymysql://user:password@host:3306/zap_workflow
DATABASE_URL=postgresql+psycopg2://user:password@host:5432/zap_workflow
```

## Integracoes

O menu de documentacao traz os fluxos e links oficiais. O resumo tambem esta em:

- [`docs/INTEGRACOES.md`](docs/INTEGRACOES.md)

## Produção

1. Rode a homologacao com o mesmo `DATABASE_URL` que vai usar em producao.
2. Cadastre estados, departamentos e usuarios.
3. Valide o webhook do WhatsApp com uma mensagem real.
4. Teste agenda, lembretes e upload de arquivos.
5. Publique a revisao no GitHub e faça o deploy da mesma versao.

## Proximos passos recomendados

1. Habilitar monitoramento de erros e logs centralizados.
2. Criar rotinas de manutencao para limpar anexos antigos.
3. Se quiser, eu posso preparar agora um fluxo de migracao formal com Flask-Migrate.

