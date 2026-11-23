# Changelog

O formato segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o projeto
usa [versionamento semântico](https://semver.org/lang/pt-BR/).

## [Não lançado]

### Adicionado
- Acabamento (`video_editor/finishing/`), guiado por um plano JSON por vídeo:
  - **Imagens sobre o vídeo**, sempre com o buraco da webcam transparente. Modos: `cover` e `contain`
    (imagem pura, upscale até 1,6x), `cutout` (pessoa recortada; com `requirements-rembg.txt` remove o
    fundo de fotos comuns), `badge` (logo num selo branco), `fill`, `panel_white`, `panel_dark` e `dark`.
  - **Telas pretas de CORREÇÃO/ATENÇÃO**, inseridas no tempo. O vídeo, o áudio e a legenda (`.srt`)
    andam junto.
  - **Tarjas de correção** no topo.
  - `python cli.py finish render JOB_ID --plan plano.json` renderiza em pedaços (NVENC quando
    disponível) e reaproveita o áudio.
  - `python cli.py finish capcut JOB_ID --plan plano.json --template PROJETO` grava uma cópia nova do
    projeto do CapCut com as camadas em faixas próprias. Não mexe no original nem nas fotos já
    colocadas, e recusa gravar com o CapCut aberto.
  - `python cli.py finish fetch-images --query ...` busca imagens livres no Wikimedia Commons e grava
    os créditos.
- Sugestões de acabamento: `python cli.py finish suggest JOB_ID` propõe, a partir da transcrição,
  do briefing e da tela, onde entram imagens, logos e memes (biblioteca local em
  `config/finishing_library.local.json`, veja o `.example.json`), tarjas ou telas pretas para
  afirmações contraditas e confirmadas, e coberturas para telas indevidas parecidas com os prints de
  `config/screen_hazards.local/`. Tudo nasce pendente: `finish review`/`finish decide` (ou a API
  `/api/jobs/{id}/finishing/...`) aceitam, recusam ou editam, e `finish plan` monta o plano só
  com o aceito. `finish describe` gera a descrição do YouTube com capítulos e créditos.
- Painel "Acabamento" no editor do Studio: gera e revisa as sugestões com prévia do quadro
  exportado e a camada aplicada, filtros, atalhos A/R/J/K, troca de imagem nas coberturas longas,
  edição do texto das correções e ajuste fino dos tempos. "Gerar vídeo final" renderiza só o que foi
  aceito, com o áudio masterizado, e entrega MP4, SRT e descrição. As imagens são servidas apenas
  quando pertencem a uma sugestão do job.
- Áudio de voz: `python cli.py finish audio analyze VIDEO` mede o loudness (LUFS), o true peak, a
  dinâmica (LRA), o ruído nas pausas (sem contar o silêncio digital), os canais e as bandas, e
  explica em português o que corrigir. `python cli.py finish audio master VIDEO --output OUT` aplica
  só o necessário: canal mudo duplicado, passa-alta, redução de ruído conservadora, de-esser,
  compressor e normalização até −14 LUFS, com pico abaixo de −1 dBTP. O vídeo não é recodificado. Uma chave
  `"audio"` no plano ou `finish render --master-audio` aplica a masterização ao final do render.
- Biblioteca que cresce sozinha: **Ampliar biblioteca** no painel Acabamento (ou
  `python cli.py finish library expand JOB_ID [--dry-run]`) busca imagens de licença livre no
  Wikimedia Commons para os nomes do vídeo que ainda não têm imagem, com título, autor, licença e
  link no crédito. Sobrenomes soltos e variações de quem já está na biblioteca são ignorados, e
  nomes de pessoas nunca são baixados sozinhos: ficam para você confirmar, para não pôr o rosto
  de um homônimo no vídeo. Recusar uma imagem automática tira ela da biblioteca.
- Som no vídeo final: whoosh quando entra uma imagem, tela preta ou tarja, "pop" nos logos e
  "boom" nos memes, sintetizados pelo FFmpeg (ou os seus, em `config/sounds.local/`), e música de
  fundo opcional, vinda de `config/music.local/`, que abaixa sozinha quando você fala. As
  coberturas de tela ficam em silêncio.
- Depois do corte automático, as sugestões de acabamento são geradas em segundo plano, já
  buscando as imagens livres que faltam (Wikimedia Commons e, como reserva, Openverse). Nomes
  sem imagem livre ficam anotados para não serem buscados de novo a cada vídeo.
- `python cli.py ledger build` reúne as decisões humanas de todos os cortes (aceitar,
  recusar, cortes e restaurações manuais) em `learning/decisions.jsonl`, fora do Git.
  `python cli.py ledger replay` compara a política atual com essas decisões: erros, perguntas,
  quanto você corta quando é perguntado e o que passou sem sugestão.
- Glossário de nomes próprios: `config/glossary.local.json` (veja
  `config/glossary.example.json`) e, com `requirements-ocr.txt`, os nomes lidos nos slides.
  `python cli.py glossary apply JOB_ID --dry-run` mostra as trocas (ex.: "Hwang" → "Huang")
  antes de gravar; a transcrição original fica em `transcript.raw.json`. Dicas ao Whisper
  (`glossary_hotwords`) ficam desligadas por padrão porque causaram repetições.
- Voz de fundo: com `requirements-voice.txt`, o corte automático compara a voz de cada trecho
  com a do criador e sugere para revisão a fala de outra pessoa (um vídeo tocando no fundo,
  por exemplo). Nunca corta sozinho. `python cli.py voice scan JOB_ID` mostra o relatório.
  O modelo (WeSpeaker ResNet34, Apache-2.0/CC-BY-4.0) é baixado na primeira vez e conferido
  por SHA-256.

### Alterado
- O vídeo final pode copiar direto do vídeo-base os trechos sem imagem entre dois quadros-chave
  e recodificar só o resto. Só acontece quando a escala de tempo do vídeo-base fecha com o fps
  (um quadro = número inteiro de unidades); os exports atuais não fecham (1/16000 a 60 fps) e
  continuam sendo recodificados por inteiro, sem os carimbos fora de ordem que a cópia geraria.
- A assistência editorial com Gemini pula modelos aposentados, espera respostas longas (até
  2 min), passa para o próximo modelo quando um está sobrecarregado ou sem cota e evita por 5
  minutos o modelo que falhou.
- O vídeo final do Acabamento agora usa a mesma qualidade e o mesmo teto de bitrate do export
  e cai automaticamente para o encoder de software quando a GPU falha.
- Mais rápido: a lista de exports e as demais telas do Studio deixaram de conferir o disco
  milhares de vezes por chamada (de 7-12 s para menos de 1 s num job longo); a leitura de texto
  da tela roda em segundo plano e não segura mais a transcrição (eram 12-15 min num vídeo de
  36 min); as prévias do Acabamento são geradas em segundo plano sem ocupar a fila de exports e
  renders; a masterização mede tudo num passe só (de cerca de 5 min para menos de 1,5 min num
  vídeo de 24 min).

### Corrigido
- O Studio instalado pelo instalador do Windows e pelo Docker não abria: o Acabamento precisa do
  Pillow, que só era instalado à parte. O Pillow agora faz parte de `requirements.txt`.
- Um vídeo final interrompido (Studio fechado no meio do render) não fica mais "renderizando"
  para sempre; o painel mostra que foi interrompido. Um segundo pedido de render enquanto o
  primeiro roda é recusado.
- O relatório da masterização mostra o loudness, o pico e a dinâmica medidos no áudio final, e o
  limitador deixa folga para o pico ficar abaixo de −1 dBTP depois do AAC.
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
