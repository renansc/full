# Nanotech FULL

Repositorio preparado para deploy da FULL com rotas dedicadas:

- `/` -> launcher principal
- `/bpa/` -> modulo BPA
- `/financeiro/` -> financeiro
- `/gpsmusical/` -> GPS Musical
- `/zap/` -> Zap Workflow, servido dentro da propria Nanotech

## Estrutura

- `app.py` serve os apps estaticos e as APIs usadas por Financeiro, GPS e BPA
- `bpa/`, `financeiro/` e `gpsmusical/` possuem `index.html` para funcionar direto na URL da rota
- `zap/` contem o sistema Flask de atendimento, vendas e prestacao de servicos que pode ser montado dentro da Nanotech
- `shared/remote-store.js` atende os modulos que usam sincronizacao remota
- `menuapps.txt` documenta os links diretos e as rotas da FULL

## Render

O `render.yaml` ja esta preparado para publicar o servico como `nanotech-full`.

Para banco externo no Render, deixe `DB_SSL=true` e prefira preencher `DB_PROVIDER`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` e `DB_PASSWORD` se nao quiser depender de uma `DATABASE_URL` completa.

O usuario do banco precisa ter permissao para criar e alterar tabelas, porque o app chama `create_all()` na inicializacao.
