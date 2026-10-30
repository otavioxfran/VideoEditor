# Segurança

O VideoEditor roda no seu computador e só aceita conexões locais (`127.0.0.1`/`localhost`).
Ele lê seus vídeos originais sem alterá-los e grava tudo o que gera na pasta `jobs/`.

## O que sai do seu computador

Nada, por padrão. A única exceção é a **assistência editorial** (Gemini, plano gratuito):
quando você a ativa e marca a autorização, o texto da transcrição (e o seu `PERSONA.md`,
se existir) é enviado ao Google. Áudio e vídeo nunca são enviados. A chave de API fica em
`config/editorial_provider.local.json`, que não vai para o Git.

## Como relatar uma vulnerabilidade

Não abra uma issue pública. Use o botão **Report a vulnerability** na aba
[Security](../../security/advisories/new) deste repositório (aviso privado do GitHub).

Inclua o que você encontrou, como reproduzir e o impacto. A resposta inicial vem em até
7 dias. Como é um projeto mantido por uma pessoa, a correção pode levar mais tempo; você
será avisado(a) antes da divulgação.

## Versões com suporte

Só a versão mais recente da branch `main` recebe correções.
