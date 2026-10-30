# Changelog

O formato segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o projeto
usa [versionamento semântico](https://semver.org/lang/pt-BR/).

## [1.1.0] — 2026-10-30

### Adicionado
- Transcrição padrão com `large-v3-turbo` (mais precisa e tão rápida quanto o `small`).
- Detecção de tomadas repetidas inteiras ("a última fica"), sempre como sugestão, mostrando
  o conteúdo que só existe nas tomadas descartadas.
- Detecção de cadeira vazia (presença visual) com evidência em imagem.
- Ritmo mais natural: sobras de silêncio entre cortes somem e pausas expressivas variam.
- Relatório de naturalidade da edição: `python cli.py report JOB_ID`.
- Nova interface: Filmoteca com posters e pendências, editor com fila de decisões, timeline
  com waveform, zoom e seleção por arrasto, atalhos de teclado e painel "Finalizar para o
  CapCut" (MP4 + SRT + marcadores).
- Documentos da comunidade, CI e avisos de terceiros.

### Mudado
- O perfil do canal e o `PERSONA.md` passam a ser locais (fora do Git); veja
  `PERSONA.example.md`.
- O Docker publica a porta só em `127.0.0.1`.

### Removido
- Artefatos internos de pesquisa e avaliação.

## [1.0.0] — 2026-10-19

- Primeira versão.
