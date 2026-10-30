---
version: alpha
name: VideoEditor
description: >-
  Estúdio local de corte para talking heads, usado por um criador em sessões longas no desktop.
  A tela é uma mesa de trabalho escura e plana (true-black-flat): fundo #08080a, painéis um degrau
  de luz acima (#101013, #17171b, #1f1f24) separados por bordas de 1px, nunca por sombra. Texto
  neutro quase branco (#f4f4f4). Uma única cor de dado, o âmbar #e8b45a, marca o que precisa de
  decisão: sugestões de revisão na timeline, contagens pendentes e foco. Vermelho, verde e azul
  aparecem só como status. Instrument Sans carrega toda a interface; IBM Plex Mono só timecodes e
  IDs. O único botão cheio da tela é quase branco e é a ação principal (Exportar, Revisar). O
  elemento assinatura é a timeline de decisão: keep, corte, revisão, protegido, waveform e playhead
  na fonte inteira.

colors:
  ground: "#08080a"
  surface: "#101013"
  raised: "#17171b"
  raised-2: "#1f1f24"
  border: "#26262c"
  border-strong: "#3a3a42"
  text: "#f4f4f4"
  muted: "#a8a8a8"
  faint: "#8d8d8d"
  accent: "#e8b45a"
  accent-strong: "#f2c879"
  accent-soft: "#2a2214"
  danger: "#e46a6a"
  danger-soft: "#2a1719"
  success: "#7bcb9a"
  success-soft: "#12251a"
  info: "#8fb8d9"
  info-soft: "#14212b"
  primary: "#ffffff"
  primary-ink: "#08080a"
  tl-keep: "#202027"
  tl-cut: "#3a1d21"
  tl-protected: "#1d3327"
  tl-wave: "#8b8b95"

typography:
  page-title:
    fontFamily: "'Instrument Sans', system-ui, sans-serif"
    fontSize: 32px
    fontWeight: 600
    lineHeight: 1.05
    letterSpacing: -0.04em
  editor-title:
    fontFamily: "'Instrument Sans', system-ui, sans-serif"
    fontSize: 20px
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: -0.01em
  panel-title:
    fontFamily: "'Instrument Sans', system-ui, sans-serif"
    fontSize: 14px
    fontWeight: 600
    lineHeight: 1.4
  body:
    fontFamily: "'Instrument Sans', system-ui, sans-serif"
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.5
  body-sm:
    fontFamily: "'Instrument Sans', system-ui, sans-serif"
    fontSize: 13px
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "'Instrument Sans', system-ui, sans-serif"
    fontSize: 13px
    fontWeight: 500
    lineHeight: 1
  meta:
    fontFamily: "'Instrument Sans', system-ui, sans-serif"
    fontSize: 12px
    fontWeight: 400
    lineHeight: 1.45
  timecode:
    fontFamily: "'IBM Plex Mono', ui-monospace, monospace"
    fontSize: 12px
    fontWeight: 400
    lineHeight: 1.4
  tick:
    fontFamily: "'IBM Plex Mono', ui-monospace, monospace"
    fontSize: 11px
    fontWeight: 400
    lineHeight: 1

rounded:
  xs: 4px
  sm: 6px
  md: 10px
  lg: 14px
  pill: 999px

spacing:
  xxs: 4px
  xs: 8px
  sm: 12px
  base: 16px
  md: 20px
  lg: 24px
  xl: 32px
  xxl: 48px

components:
  topbar:
    backgroundColor: "{colors.ground}"
    textColor: "{colors.text}"
    height: 56px
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.primary-ink}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    height: 34px
    padding: 0 12px
  button-secondary:
    backgroundColor: transparent
    textColor: "{colors.text}"
    borderColor: "{colors.border-strong}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    height: 34px
  button-ghost:
    backgroundColor: transparent
    textColor: "{colors.muted}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    height: 34px
  button-small:
    typography: "{typography.meta}"
    height: 28px
    padding: 0 10px
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    borderColor: "{colors.border-strong}"
    rounded: "{rounded.sm}"
    height: 36px
  badge:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.muted}"
    typography: "{typography.meta}"
    rounded: 5px
    height: 22px
  badge-review:
    backgroundColor: "{colors.accent-soft}"
    textColor: "{colors.accent}"
  panel:
    backgroundColor: "{colors.surface}"
    borderColor: "{colors.border}"
    rounded: "{rounded.md}"
    padding: 16px
  tab:
    textColor: "{colors.muted}"
    activeTextColor: "{colors.text}"
    activeIndicator: "2px {colors.accent}"
    typography: "{typography.label}"
    height: 44px
  segmented:
    backgroundColor: "{colors.ground}"
    activeBackground: "{colors.raised-2}"
    rounded: "{rounded.sm}"
    height: 30px
  job-card:
    backgroundColor: "{colors.surface}"
    borderColor: "{colors.border}"
    rounded: "{rounded.md}"
    poster: 16:9
  review-card:
    backgroundColor: "{colors.surface}"
    borderColor: "{colors.border}"
    accentEdge: "3px {colors.accent}"
    rounded: "{rounded.sm}"
    padding: 12px
  timeline:
    backgroundColor: "{colors.ground}"
    borderColor: "{colors.border}"
    rounded: "{rounded.sm}"
    height: 112px
  dialog:
    backgroundColor: "{colors.surface}"
    borderColor: "{colors.border-strong}"
    rounded: "{rounded.lg}"
    shadow: "0 18px 60px rgba(0,0,0,.55)"
---

# VideoEditor — DESIGN.md (v2.1)

## Overview

Um estúdio local de corte para os vídeos talking head do canal. O vídeo é o material; **a decisão de corte é o produto**. A interface é uma mesa de trabalho, não um dashboard de marketing nem uma landing page.

- **Quem usa:** um criador, sozinho, no desktop (1440 px é o caso principal), revisando vídeos de 10–30 min.
- **Trabalho principal:** escolher um raw, rodar o corte, revisar poucas decisões de alto valor e exportar MP4 + SRT + markers para terminar no CapCut.
- **Pergunta da Filmoteca:** "qual vídeo precisa de mim agora?"
- **Pergunta do Editor:** "o que falta decidir para este corte ficar pronto?"
- **Pergunta de Configurações:** "o estúdio está pronto para cortar?"
- **Ideia:** revisar é ler a edição na timeline, não rolar uma lista. Cada keep, corte, tomada pendente e ausência é uma marca na fonte.

**Características principais**
- Escuro neutro e plano; elevação por degraus de luz (`ground → surface → raised → raised-2`), nunca por sombra.
- Uma cor de dado: âmbar `{colors.accent}` = "precisa de você". Nunca vira preenchimento de chrome.
- Um primário por tela, quase branco. Todo o resto é contorno ou ghost.
- Instrument Sans em tudo; IBM Plex Mono só onde o número precisa alinhar dígito a dígito (timecodes, IDs, ticks).
- Densidade de ferramenta: texto de leitura 13–14 px, meta 12 px, nada abaixo de 11 px.

**Referências e precedência.** O estilo veio do Taste Skill (`true-black-flat`, regras de app screen em `references/app-screens.md`). A estrutura deste arquivo segue o spec DESIGN.md (alpha) do getdesign.md. A análise do Linear no catálogo serviu de referência para a escada de superfícies escuras e para a disciplina de um acento só; nada da identidade do Linear é copiado. Em caso de conflito vale esta ordem: **este arquivo → regras de app screen do Taste → referência externa**.

## Colors

### Superfícies (escada de luz)
- **Ground** (`{colors.ground}` #08080a): fundo da aplicação e da timeline. Não usar `#000` puro.
- **Surface** (`{colors.surface}` #101013): painéis, cards, dialogs, review cards pendentes.
- **Raised** (`{colors.raised}` #17171b): hover, wells, badges neutros, esqueletos de carregamento.
- **Raised 2** (`{colors.raised-2}` #1f1f24): controle ativo (segmented), seleção neutra.

Não pule degraus: um elemento sobre `surface` usa `raised`, não `raised-2`.

### Bordas
- **Border** (`{colors.border}` #26262c): 1px em painéis, cards, tabs, divisores.
- **Border strong** (`{colors.border-strong}` #3a3a42): controles (botões secundários, inputs) e foco neutro.

Borda **ou** degrau de tom, nunca borda + sombra.

### Texto
- **Text** (`{colors.text}` #f4f4f4): títulos, rótulos, texto principal.
- **Muted** (`{colors.muted}` #a8a8a8): texto secundário, botões ghost.
- **Faint** (`{colors.faint}` #8d8d8d): metadados, contagens, dicas. ~6:1 sobre o ground; não usar abaixo de 12 px.

### Dado e status
- **Accent** (`{colors.accent}` #e8b45a): o que precisa de decisão. Marcas de revisão na timeline, contagem pendente, borda esquerda do review card, indicador de aba ativa, anel de foco.
- **Accent soft** (`{colors.accent-soft}` #2a2214): fundo do badge de revisão.
- **Danger** (`{colors.danger}` #e46a6a) / **soft** (#2a1719): falha, corte destrutivo, linha de corte. Uma marca vermelha por objeto, no máximo.
- **Success** (`{colors.success}` #7bcb9a) / **soft** (#12251a): exportado e atual, aplicado, protegido.
- **Info** (`{colors.info}` #8fb8d9) / **soft** (#14212b): informação neutra, raramente.

### Timeline
- **Keep** (`{colors.tl-keep}` #202027): faixa do que fica.
- **Cut** (`{colors.tl-cut}` #3a1d21) + linha `{colors.danger}` a 60%: faixa do que sai.
- **Protected** (`{colors.tl-protected}` #1d3327): pausa protegida.
- **Wave** (`{colors.tl-wave}` #8b8b95) a 55%: waveform RMS.
- Revisão = `{colors.accent}`; sugestão automática = `{colors.border-strong}` a 65%.

### Primário
- **Primary** (`{colors.primary}` #ffffff) com **primary ink** (`{colors.primary-ink}` #08080a): o único botão cheio da tela.

## Typography

### Famílias
- **Instrument Sans** (400/500/600/700, local em `ui/minimal/fonts/`): toda a interface.
- **IBM Plex Mono** (400/500, local): timecodes, IDs, ticks da régua e atalhos (`kbd`). Nunca em labels, botões ou contagens em prosa.
- Instrument Sans 400 aponta hoje para o arquivo 500 (o 400 vendorizado não renderizava acentos). Se trocar a fonte, valide á ã ç ê í ó ú no navegador.

### Hierarquia

| Token | Tamanho | Peso | Uso |
|---|---|---|---|
| `{typography.page-title}` | 32 px | 600 | h1 da Filmoteca (cai para 26/24 em 768/390) |
| `{typography.editor-title}` | 20 px | 600 | h1 do Editor (nome do raw) no topbar |
| `{typography.panel-title}` | 14 px | 600 | h2 de painel, título de card |
| `{typography.body}` | 14 px | 400 | texto corrido, motivo da sugestão |
| `{typography.body-sm}` | 13 px | 400 | texto denso de painel |
| `{typography.label}` | 13 px | 500 | botões, tabs, rótulos |
| `{typography.meta}` | 12 px | 400 | metadados, badges, dicas, botões pequenos |
| `{typography.timecode}` | 12 px | 400 | timecodes, IDs, relógio do player (mono, tabular) |
| `{typography.tick}` | 11 px | 400 | régua da timeline e `kbd`, só isso |

### Princípios
- Números em colunas e contadores com `font-variant-numeric: tabular-nums`.
- Sentence case sempre; nenhuma caixa alta espaçada na interface hoje.
- Frases de ajuda dentro de dialogs usam 13 px (são lidas, não escaneadas); a evidência do review card ("Só aqui", fala do trecho) também.
- Sem palavra destacada em título, sem eyebrow, sem 01/02/03 decorativo.

## Layout

### Espaço
- Base 4 px: `{spacing.xxs}` 4 · `{spacing.xs}` 8 · `{spacing.sm}` 12 · `{spacing.base}` 16 · `{spacing.md}` 20 · `{spacing.lg}` 24 · `{spacing.xl}` 32 · `{spacing.xxl}` 48.
- Canvas com gutter 24 px no desktop, 16 px no telefone.

### Grade
- **Shell:** topbar de 56 px com marca, caminho e ações; **sem sidebar**. O command palette (`Ctrl/Cmd+K`) substitui navegação.
- **Filmoteca:** largura máxima 1440 px; grade de cards com contagem por largura (3 em 1440, 2 em 768, 1 em 390); seções Precisa de você / Processando / Prontos / Antigos.
- **Editor:** janela inteira. Coluna principal (viewer + timeline) e rail de revisão à direita (~420 px). O rail e a coluna principal terminam na mesma linha.
- Uma borda esquerda e uma direita por tela; sem metade vazia; sem faixa morta ao lado de um bloco.

## Elevation & Depth

- Profundidade é luz: cada nível acima do ground é um degrau de tom mais claro.
- Sombra só no que flutua: dialog, command palette, menu, toast (`0 18px 60px rgba(0,0,0,.55)`).
- Topbar sticky opaco (`ground` a 92%), sem blur.
- Sem glow, glass, gradiente ou sombra em repouso. O único "brilho" permitido é o anel de foco âmbar.

## Shapes

| Token | Valor | Uso |
|---|---|---|
| `{rounded.xs}` | 4 px | kbd, botões dentro de segmented, barras de esqueleto |
| `{rounded.sm}` | 6 px | botões, inputs, review cards, timeline |
| `{rounded.md}` | 10 px | painéis e job cards |
| `{rounded.lg}` | 14 px | dialogs |
| `{rounded.pill}` | 999 px | só pontos de status (8 px) |

## Components

### Topbar
Marca (SVG de splice em `text`), caminho ("Filmoteca" / nome do raw + status), ações à direita: comandos (`Ctrl K`), Desfazer, e o primário da tela. No Editor o primário é **Exportar MP4 + SRT**; na Filmoteca é **Novo corte**.

### Botões
- **Primário** (`{component.button-primary}`): um por tela. Ícone 16 px + label.
- **Secundário** (`{component.button-secondary}`): contorno `border-strong`, para ações reais mas não principais.
- **Ghost** (`{component.button-ghost}`): sem borda, texto `muted`; ações frequentes e baratas (Ouvir, Manter).
- **Pequeno** (`{component.button-small}`): 28 px de altura, 12 px de texto. Nada menor que isso.
- **Destrutivo:** contorno `danger`, só em contexto (apagar versão).
- Ícone sozinho só com `aria-label` e tooltip.

### Badges
22 px, 12 px de texto, raio 5. `badge-review` (âmbar) para categorias que pedem decisão; `success` para aplicado/exportado; `neutral` para automáticos e retakes genéricos. Nunca acima de um título; nunca repetindo o que a seção já diz.

### Job card (Filmoteca)
Poster 16:9 real (`/api/jobs/{id}/poster`), duração em mono sobre o poster, título (nome do raw, com o caminho completo no tooltip), **linha de status** a 13 px (decisões pendentes em âmbar · estado do export), **linha de referência** a 12 px (modelo · versões · "job antigo"), e uma ação secundária de largura total (**Revisar** quando há pendências, **Abrir editor** quando não). O único primário da Filmoteca é **Novo corte**. O badge sobre o poster só aparece para estados que não são "pronto" (processando, falhou, legacy).

O estado do export é honesto: **exportado** em `success` só se o export é atual; **export desatualizado** em `muted` com tooltip quando o corte ou o perfil mudou depois.

### Review card (Editor) — `ui/minimal/review-card.js`
1. Linha 1: badge de categoria (Tomada repetida, Ausência, Falso começo, Ideia repetida, Recomeço) + intervalo em `{typography.timecode}`, separado do texto.
2. Motivo em `{typography.body}`, sem repetir a categoria.
3. Evidência:
   - **Take stack** para `repeated_block`: chips "Tomada 1 / 2 / 3", a que fica em destaque, e o conteúdo perdido em 12 px.
   - **Frame** para `visual_absence`: miniatura 16:9 de `/api/jobs/{id}/frame?t=` ao lado do motivo.
4. Ações, em uma linha só: **Aplicar corte** (secundário, ícone tesoura), **Ouvir** (ghost, fones), **Manter** (ghost), e **Proteger** dentro de um menu `⋯`.
5. Borda esquerda de 3 px em âmbar enquanto pendente; verde quando aplicado; neutra quando mantido. O badge vira "Aplicado", "Mantido" ou "Protegido" e a categoria passa para a direita. Num card aplicado, a ação principal vira **Restaurar**.
   A decisão vem de `decisions` em `GET /api/jobs/{id}/edit` (histórico reproduzido como a pilha de desfazer); a contagem pendente da Filmoteca usa a mesma regra.
6. Teclado: ↑/↓ navega, Enter ouve, A aplica, R mantém. O card selecionado acende a marca correspondente na timeline e vice-versa.

### Tabs e filtros
Tabs com underline âmbar de 2 px e contagem (mono só no número) no ativo. Filtros como segmented (Revisar / Automáticos / Todos). Contagens seguem o filtro atual e são recalculadas a cada troca.

### Timeline de decisão (elemento assinatura)
- SVG da fonte inteira: régua com ticks mono, faixas keep/cut/protected, waveform, marcas de revisão, playhead de 1 px em `text`.
- Clique busca; o range `#seek` continua como fallback acessível.
- Zoom Ajustar/2×/4× (segmented no cabeçalho); com zoom, a timeline rola dentro do próprio frame e o playhead fica na vista durante a reprodução.
- Hover mostra uma linha âmbar e o timecode `m:ss.d`.
- Arrastar seleciona um trecho (faixa âmbar a 14%), busca o início e preenche Início/Fim do Corte manual; quem aplica é o botão "Cortar intervalo". Clique simples limpa a seleção.
- Marca selecionada fica destacada e é rolada para a vista; clicar numa marca abre o card.
- Toda marca tem tooltip com motivo e duração.
- Um transporte só: o player customizado (play, ±2 s, range). Sem os controles nativos do `<video>`.

### Estados
- **Carregando:** o layout final com barras planas em `raised` no tamanho real, sem shimmer; contagens mostram "–" em `faint`, nunca "0".
- **Vazio:** mantém o frame e o título e diz em uma frase o próximo passo.
- **Erro:** dentro do bloco que falhou, uma frase com o que fazer e um botão de tentar de novo.
- **Processando:** o Editor diz "Aguardando o preparo"; a Filmoteca põe o card em Processando.

### Pronto para cortar (`ui/minimal/readiness.js`)
Uma checagem só, mostrada em dois lugares: um aviso no topo da Filmoteca (borda âmbar, lista
só o que falta, some quando tudo está pronto) e a seção **Este computador** nas
Configurações (lista completa). Cada linha tem ícone de status (`check-circle` verde ou
`warning-circle` âmbar), título a 13 px/600, explicação a 13 px em `muted` e, quando houver,
uma ação secundária à direita ("Baixar (1,5 GB)"). O download mostra uma barra plana de 6 px
em `raised` com preenchimento âmbar e o texto "x de y".

### Modo simples e avançado
O modo simples é o padrão: elementos com `data-advanced` somem (edição experimental,
avaliação humana, briefing, medição de tempo, caminho digitado, "Só transcrever"). A
preferência é por navegador (`localStorage`) e liga em **Configurações → Interface** ou no
command palette.

### Dialog, command palette e toast
Dialog em `surface` com borda `border-strong`, raio 14, sombra; foco preso; Esc fecha. Toast em uma linha, canto inferior, `aria-live`, sem cobrir controles.

## Do's and Don'ts

### Do
- Usar o âmbar só para "precisa de você" e para foco.
- Mostrar poucas decisões de alto valor; retakes genéricos ficam em "Todos".
- Mostrar a evidência (frame, tomadas, conteúdo perdido) junto da decisão.
- Usar Phosphor (regular, local em `ui/minimal/icons.svg`) a 14–16 px, sempre ao lado de um label.
- Manter texto em português do Brasil, direto: "Revisar", "Aplicar corte", "Manter", "Exportar MP4 + SRT", "Tomada 1 de 3".

### Don't
- Sem gradiente, glass, glow, sombra em repouso.
- Sem emoji em lugar nenhum da interface (usar ícones Phosphor).
- Sem monospace em labels, botões ou frases.
- Sem eyebrow, pill acima de título ou caixa alta espaçada (fora a exceção única).
- Sem cards idênticos com ícone em quadrado colorido; sem KPI decorativo.
- Sem cores fixas no JS ou CSS de componente: tudo sai de `css/tokens.css`.
- Sem componentes React/Tailwind; padrões do 21st.dev só como referência, portados para vanilla.
- Sem "Rough cut virtual", "Minimal V1" ou jargão interno na interface.

## Responsive Behavior

| Faixa | Largura | Mudanças |
|---|---|---|
| Desktop | ≥1200 px | rail de revisão à direita; Filmoteca 3 colunas |
| Desktop estreito | 1000–1199 px | rail mais estreito |
| Tablet | 721–999 px | rail vai para baixo do viewer; Filmoteca 2 colunas |
| Telefone | ≤720 px | uma coluna; ações do topbar viram ícones; o rail de revisão sobe para logo abaixo do viewer |

- Alvos de toque ≥ 44 px no telefone; texto de dado ≥ 14 px.
- A página nunca rola de lado; a timeline rola dentro do próprio frame quando houver zoom.
- Teste real de 390 px só pelo Taste Inspector (`--app`), não pelo Chrome headless (viewport mínimo ~500 px).

## Acessibilidade

- Todo controle com label visível ou `aria-label`; ícones decorativos `aria-hidden`.
- Foco visível âmbar em todos os controles; foco preso em dialogs.
- Contraste AA; `faint` nunca abaixo de 12 px.
- Teclado: Espaço/K play, J/L ±2 s, M marcador, Ctrl+Z desfazer, Esc fecha; no rail ↑/↓, Enter, A, R.
- `prefers-reduced-motion` remove deslocamentos.

## Motion

Só feedback: hover/press/foco 120 ms, dialog/sheet 180–220 ms, `cubic-bezier(.22,1,.36,1)`. Sem entradas animadas. Movimento nunca esconde falta de conteúdo.

## Iteration Guide

1. Mude um componente por vez e cite-o pelo nome em `components:`.
2. Antes de criar uma superfície, decida em qual degrau da escada ela vive.
3. Cor nova? Primeiro tente um token existente; se precisar, adicione em `css/tokens.css` **e** aqui.
4. Cada fase passa por: `python -m pytest -q`, `node --input-type=module --check`, `lint.mjs --app ui/minimal` e `inspect.mjs --app` no Studio e no Editor com **0 FAIL**, e capturas antes/depois em `scratch/ui-audit/`.
5. Avisos do inspector só ficam se der para defender em uma frase (anote em Known Gaps).

## Known Gaps

Itens conhecidos em 2026-10-30, com a fase do plano que resolve cada um:

- ~~Fase C~~ (entregue em 2026-10-30): review card em `ui/minimal/review-card.js` com ações em hierarquia, take stack, frame de evidência, estado decidido (via `decisions` do backend) e atalhos. Pendente: Aplicar/Manter não foram testados no navegador num job real (cobertos por testes de backend), porque o único job com tomadas é o 22-22-22, ainda sem revisão.
- ~~Fase D~~ (entregue em 2026-10-30): um transporte só (play, ±2 s, range, som, tela cheia; clique no vídeo alterna play); timeline com zoom Ajustar/2×/4× e rolagem própria, playhead que acompanha, hover com timecode `m:ss.d`, seleção por arrasto que preenche o Corte manual (o corte só é aplicado pelo botão), e a marca selecionada rola para a vista. Ainda falta: atalhos de zoom no teclado.
- ~~Fase E~~ (entregue em 2026-10-30): "Ritmo e prolixidade" virou a aba **Ritmo** do rail; Export, Marcadores e "Medir meu ganho de tempo" viraram o painel **Finalizar para o CapCut** (export atual com status honesto e downloads, histórico recolhido, marcadores com o tempo do MP4 e "Copiar lista"); `editor.js` reescrito legível e dividido em `editor-finish.js`, `editor-prolixity.js` e `editor-briefing.js`. Ainda falta: o export não gera arquivo de marcadores; a lista copiada é texto `HH:MM:SS rótulo` para colar no CapCut à mão.
- ~~Fase F~~ (entregue em 2026-10-30): caminho do raw só no tooltip do título; card com linha de status a 13 px (decisões · export) e linha de referência a 12 px (modelo · versões); "Revisar" virou secundário (o único primário da Filmoteca é Novo corte); marca em sentence case ("Estúdio local"); h1 do Editor em 20 px; timecodes a 12 px; evidência e ações do review card a 13 px. Inspector: 0 FAIL e 0 aviso no Studio e no Editor.
- O inspector conta texto dentro de elementos escondidos (cards fora do filtro, abas e dialogs fechados). Medindo só o visível, o Editor tem ~51% do texto abaixo de 13 px (badges, timecodes, meta): densidade de ferramenta, aceita.
- Aviso do inspector aceito: a rail do editor pode terminar depois do viewer porque a fila rola por dentro.

## Critérios de aceite

- A Filmoteca responde "qual vídeo precisa de mim" sem rolar: poster real, duração, estado, pendências e export honesto.
- O Editor abre em "Revisar" com as decisões curadas (no 22-22-22: 2 tomadas + 1 ausência + falsos começos/ideias), não com 200 cortes automáticos.
- A timeline mostra keep/cut/revisão/protegido e o playhead; clicar busca; o range acessível funciona.
- Nenhuma função existente quebra; API e IDs preservados; testes verdes.
- 1440/768/390 sem overflow; foco, contraste, reduced motion; **0 FAIL** no lint e no inspector do Taste.
