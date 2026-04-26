# Conformidade Portaria 671/2021

## Base oficial consultada

- Portaria MTP nº 671/2021, versao compilada publicada no portal gov.br e encontrada com compilacao de `26/09/2024`.
- Pagina oficial `Registro Eletronico de Ponto - REP`, atualizada em `27/03/2025`.
- Perguntas e Respostas oficiais do MTE sobre REP, consultadas em `22/04/2026`.
- Leiaute oficial do AFD em PDF disponibilizado no gov.br.
- Servidores publicos NTP da Hora Legal Brasileira do Observatorio Nacional divulgados em noticia oficial de `27/06/2023`.

## O que o projeto implementa

- `Art. 78`: o sistema foi estruturado como programa executado em servidor dedicado/nuvem, com foco exclusivo no registro de jornada e nos documentos decorrentes.
- `Art. 79`: o comprovante contem titulo, NSR, identificacao do empregador, local de prestacao, trabalhador, data/hora, registro INPI do REP-P e hash SHA-256.
- `Art. 80`: o comprovante pode ser extraido em PDF e entregue via portal; ha tambem fluxo de envio por e-mail.
- `Art. 81`: o sistema gera AFD em texto com ordenacao por NSR, sem linhas em branco e com assinatura destacada em linha final conforme orientacao oficial para REP-P.
- `Anexo V`: o AFD gerado utiliza leiaute `003`, hash SHA-256 no registro tipo `7` e trailer com contagem por tipo.
- `Anexo IX`: as marcacoes usam coletores `01`, `02`, `04` e `05`, cobrindo mobile, browser e dispositivo eletronico/RFID.
- Integridade: cada batida referencia o hash anterior, criando uma cadeia de verificacao.
- Logs: toda operacao administrativa relevante gera auditoria separada.

## Pendencias externas para homologacao formal

- Registro do programa no INPI, exigido pelo `art. 91`.
- Assinatura qualificada ICP-Brasil do AFD em `CAdES` destacado `.p7s`.
- Assinatura qualificada ICP-Brasil dos comprovantes PDF em `PAdES`.
- Assinatura do Atestado Tecnico e Termo de Responsabilidade pelo responsavel legal e tecnico, como exige o `art. 89`.

## Servidores NTP oficiais utilizados

Os enderecos divulgados oficialmente pelo Observatorio Nacional e configurados no projeto sao:

- `200.20.186.75`
- `200.20.186.94`
- `200.20.224.100`
- `200.20.224.101`

## Observacao de responsabilidade

Esta base acelera a homologacao e reduz o gap tecnico de conformidade, mas a comprovacao final de atendimento legal depende dos atos formais externos acima.
