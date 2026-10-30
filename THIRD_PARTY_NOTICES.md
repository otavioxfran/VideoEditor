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

## Baixados ou instalados à parte (não incluídos)

| Componente | Como chega | Licença |
|---|---|---|
| FFmpeg | instalado pela pessoa usuária (ou pelo sistema) | LGPL 2.1+ / GPL 2+, conforme a build |
| Modelos Whisper (`large-v3-turbo` e outros) | baixados do Hugging Face no primeiro uso | MIT (OpenAI Whisper) |
| faster-whisper, CTranslate2 | `pip` | MIT |
| FastAPI, Uvicorn, Pydantic, NumPy | `pip` | MIT / BSD |
| OpenCV (`opencv-python-headless`) | `pip` | Apache 2.0 |
| google-genai (opcional, assistência editorial) | `pip` | Apache 2.0 |

Se algum dia um instalador passar a embutir o FFmpeg, a build escolhida e a licença dela
precisam ser listadas aqui, com o link para o código-fonte correspondente.
