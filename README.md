# Nanotech FULL

Repositorio preparado para deploy da FULL com rotas dedicadas:

- `/` -> launcher principal
- `/bpa/` -> modulo BPA
- `/financeiro/` -> financeiro
- `/gpsmusical/` -> GPS Musical

## Estrutura

- `app.py` serve os apps estaticos e as APIs usadas por Financeiro, GPS e BPA
- `bpa/`, `financeiro/` e `gpsmusical/` possuem `index.html` para funcionar direto na URL da rota
- `shared/remote-store.js` atende os modulos que usam sincronizacao remota
- `menuapps.txt` documenta os links diretos e as rotas da FULL

## Render

O `render.yaml` ja esta preparado para publicar o servico como `nanotech-full`.
