# Avisos de terceiros

O VideoEditor é distribuído sob a [licença MIT](LICENSE). Ele inclui ou usa os seguintes
componentes de terceiros, cada um sob a sua própria licença.

## Incluídos neste repositório

| Componente | Onde | Licença |
|---|---|---|
| YuNet face detector (`face_detection_yunet_2023mar.onnx`), OpenCV Zoo, © 2020 Shiqi Yu | `config/models/` | MIT |
| Instrument Sans, © 2022 The Instrument Sans Project Authors | `ui/minimal/fonts/` | SIL Open Font License 1.1 (`LICENSE-1.txt`) |
| IBM Plex Mono, © 2017 IBM Corp. | `ui/minimal/fonts/` | SIL Open Font License 1.1 (`LICENSE-2.txt`) |
| Phosphor Icons, © 2023 Phosphor Icons | `ui/minimal/icons.svg` | MIT (`icons-LICENSE.txt`) |

## Incluídos no instalador para Windows

O `VideoEditor-Setup-*.exe` (gerado por `packaging/windows/build.ps1`) embute, além do código
deste repositório:

| Componente | Onde fica após instalar | Licença |
|---|---|---|
| Python (distribuição "embeddable" oficial do python.org) | `python\` | Python Software Foundation License |
| FFmpeg e FFprobe, build "release essentials" de [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) | `ffmpeg\bin\` | GPL v3 (`ffmpeg\LICENSE.txt`). Versão e configuração da build em `ffmpeg\README.txt`; código-fonte em <https://github.com/FFmpeg/FFmpeg> na tag dessa versão. O FFmpeg roda como programa separado. |
| Bibliotecas Python listadas em `requirements.txt` e `requirements-editorial.txt` | `python\Lib\site-packages\` | as licenças de cada pacote (tabela abaixo) |
| NVIDIA cuBLAS e cuDNN, redistribuídos dentro do pacote `ctranslate2` (componente opcional "Aceleração NVIDIA") | `python\Lib\site-packages\ctranslate2\` | licenças de redistribuição da NVIDIA (CUDA EULA e cuDNN SLA) |

## Baixados ou instalados à parte (não incluídos)

| Componente | Como chega | Licença |
|---|---|---|
| FFmpeg (instalação pelo código-fonte ou Docker) | instalado pela pessoa usuária (ou pelo sistema) | LGPL 2.1+ / GPL 2+, conforme a build |
| Modelos Whisper (`large-v3-turbo` e outros) | baixados do Hugging Face no primeiro uso | MIT (OpenAI Whisper) |
| faster-whisper, CTranslate2 | `pip` | MIT |
| FastAPI, Uvicorn, Pydantic, NumPy | `pip` | MIT / BSD |
| OpenCV (`opencv-python-headless`) | `pip` | Apache 2.0 |
| google-genai (opcional, assistência editorial) | `pip` | Apache 2.0 |

Ao trocar a build do FFmpeg ou a versão do Python do instalador, atualize esta página.
