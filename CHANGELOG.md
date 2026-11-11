# Changelog

O formato segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o projeto
usa [versionamento semântico](https://semver.org/lang/pt-BR/).

## [Não lançado]

### Adicionado
- `python cli.py ledger build` reúne as decisões humanas de todos os cortes (aceitar,
  recusar, cortes e restaurações manuais) em `learning/decisions.jsonl`, fora do Git.
  `python cli.py ledger replay` compara a política atual com essas decisões: erros, perguntas,
  quanto você corta quando é perguntado e o que passou sem sugestão.

### Corrigido
- Ausência na frente da câmera (cadeira vazia) agora funciona em gravação de tela com a
  webcam num canto: a comparação usa só a região da pessoa, e a referência de cena vazia
  precisa estar parada. Pessoa de cabeça baixa ou inclinada não vira mais ausência, e
  ausências com menos de 3 s são ignoradas. Quando não dá para decidir, a análise registra
  quanto tempo havia rosto em cena.

## [1.2.0] — 2026-10-30

### Adicionado
- Instalador para Windows (`VideoEditor-Setup-*.exe`): instala por usuário, sem
  administrador, com Python e FFmpeg embutidos, atalho no menu Iniciar e componente opcional
  de aceleração NVIDIA. Gerado pelo GitHub Actions a cada release.
- **Configurações → Encerrar o estúdio** na versão instalada.
- Escolher qualquer vídeo do computador pelo diálogo nativo do sistema (**Novo corte →
  Escolher vídeo no computador…**) e trocar a pasta de vídeos nas Configurações.
- "O estúdio está pronto?": checagem de FFmpeg, placa de vídeo, modelo e espaço em disco,
  com download do modelo e barra de progresso na primeira vez. Sem placa NVIDIA, o modelo
  `small` é escolhido.
- Modo simples (padrão) que esconde edição experimental, avaliação, briefing e medição de
  tempo; **Configurações → Interface** liga o modo avançado.

### Mudado
- Instalado numa pasta somente leitura (ou com `VIDEOEDITOR_INSTALLED`), o editor guarda
  cortes, configurações e `PERSONA.md` em `%LOCALAPPDATA%\VideoEditor`.
  `VIDEOEDITOR_DATA_DIR` define outra pasta.

### Corrigido
- `start-studio.cmd` chama o `start-studio.local.cmd` pelo caminho completo (alguns Windows
  não executam comandos da pasta atual).

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
