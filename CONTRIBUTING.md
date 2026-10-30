# Como contribuir

Obrigado por querer ajudar! O VideoEditor é um editor de corte local para vídeos de
pessoa falando para a câmera (talking head). Ele corta silêncios, tropeços e tomadas
repetidas e deixa as decisões duvidosas para você revisar.

*English speakers: issues and pull requests in English are welcome too.*

## Antes de começar

- Procure nas [issues](../../issues) se o assunto já existe.
- Para mudanças grandes, abra uma issue antes do pull request para combinarmos a ideia.
- Bugs: use o modelo de bug e diga sistema operacional, GPU (ou "sem GPU") e o que
  apareceu na tela ou no terminal.

## Rodando o projeto

Pré-requisitos: Python 3.11+, FFmpeg no PATH e Node 18+ (só para checar os módulos JS).

```bash
pip install -r requirements-dev.txt
python cli.py studio          # abre o Studio em http://127.0.0.1:8765
python -m pytest -q           # testes
```

Coloque vídeos de teste numa pasta e aponte `SOURCE_VIDEOS_DIR` para ela. Os vídeos
originais nunca são alterados; tudo o que o editor gera fica em `jobs/`.

## Instalador para Windows

`packaging/windows/build.ps1` monta o Python embutido, as dependências, o FFmpeg e o app em
`build/windows/stage` e, com o [Inno Setup 6](https://jrsoftware.org/isinfo.php) instalado,
gera o `.exe`. O workflow `Windows installer` faz o mesmo no GitHub e anexa o instalador a
cada release publicada.

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
```

## Regras da casa

- **Privacidade primeiro.** Nada de vídeo, áudio ou transcrição sai do computador, a não
  ser que a pessoa ative a assistência editorial e autorize o envio de texto.
- **Nunca cortar fala com conteúdo sem revisão.** Corte automático só vale para silêncio,
  ausência sem fala e tropeço ou retake idêntico. O resto vira sugestão.
- **Interface:** siga o [`ui/DESIGN.md`](ui/DESIGN.md) (tokens, componentes, o que não fazer).
  Sem frameworks: HTML, CSS e JS puros.
- **Testes:** todo comportamento novo vem com teste. `python -m pytest -q` precisa passar.
- **Não suba dados pessoais:** vídeos, transcrições reais, chaves de API e o seu
  `PERSONA.md` ficam fora do Git (já estão no `.gitignore`).

## Pull requests

1. Faça um fork e crie uma branch a partir da `main`.
2. Mantenha o PR focado em um assunto.
3. Descreva o que mudou e como você testou (inclua um print se mexeu na interface).
4. O CI precisa passar.

Ao contribuir, você concorda que sua contribuição é licenciada sob a [licença MIT](LICENSE)
do projeto e segue o nosso [código de conduta](CODE_OF_CONDUCT.md).
