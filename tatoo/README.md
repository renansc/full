# Tatoo Studio Web

Base web para estudio de tatuagem com:

- cadastro de clientes
- agenda e sessoes
- geracao de consentimentos
- assinatura digital em tela touch
- financeiro simples
- exportacao e importacao em JSON

## Como usar

1. Abra `index.html` em um navegador moderno.
2. Cadastre clientes, sessoes, termos e pagamentos.
3. Gere um consentimento e abra o link de `signature.html?consentId=...`.
4. No iPad ou tablet Android, o cliente pode assinar com o dedo ou caneta touch.
5. Os dados ficam salvos no `localStorage` do navegador e podem ser exportados em JSON.

## Observacoes importantes

- Nesta versao, o armazenamento e local no navegador, sem backend.
- Para usar em outro dispositivo e compartilhar a mesma base, o ideal e servir a pasta em um servidor web interno ou hospedar a aplicacao.
- A estrutura foi organizada para facilitar uma proxima etapa com API, banco de dados e autenticacao.
