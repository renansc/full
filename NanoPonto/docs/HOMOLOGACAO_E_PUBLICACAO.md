# Homologacao e Publicacao

## Homologacao recomendada

1. Preencher os dados legais da empresa, responsavel tecnico e registro INPI no painel web.
2. Cadastrar funcionarios com CPF, codigo interno e RFID, quando houver.
3. Validar a consulta da Hora Legal Brasileira com conectividade real aos servidores do Observatorio Nacional.
4. Gerar um AFD de amostra e conferir o leiaute com a equipe de DP/RH e com o juridico trabalhista.
5. Integrar o certificado ICP-Brasil para assinatura PAdES e CAdES antes da entrada em producao.
6. Assinar o Atestado Tecnico e Termo de Responsabilidade e arquivar o PDF final.

## Publicacao no GitHub

1. Trabalhar em branch dedicada do repositorio `nanotech`.
2. Subir apenas o diretoria `NanoPonto` e eventuais ajustes relacionados.
3. Abrir PR em modo draft com foco em:
   - base REP-P
   - AFD
   - comprovantes
   - modulo RH
   - documentacao de conformidade

## Promocao para producao depois

- publicar primeiro em homologacao
- executar testes reais com celular e RFID
- validar exportacao AFD e comprovantes nas ultimas 48 horas
- revisar politicas de backup e retencao
- liberar somente apos assinatura formal e aprovacao da empresa
