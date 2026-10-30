"""Local Gemini setup. Public responses contain no credential or credential hash."""

from pathlib import Path
from threading import Lock
import importlib.util

from fastapi import APIRouter
from pydantic import Field, SecretStr
from video_editor import storage
from video_editor.models import Request
from video_editor.editorial.llm_client import FREE_MODELS

_lock = Lock()


class ProviderSettings(Request):
    api_key: SecretStr | None = None
    enabled: bool = Field(default=False, strict=True)
    free_tier_confirmed: bool = Field(default=False, strict=True)
    text_consent: bool = Field(default=False, strict=True)


def public_settings(path):
    path = Path(path)
    config = storage.read(path) if path.exists() else {}
    try:
        sdk = importlib.util.find_spec("google.genai") is not None
    except (ImportError, ModuleNotFoundError):
        sdk = False
    return dict(
        enabled=config.get("enabled") is True,
        configured=bool(config.get("api_key")),
        free_tier_confirmed=config.get("free_tier_confirmed") is True,
        text_consent=config.get("text_consent") is True,
        model=FREE_MODELS[0],
        sdk_available=sdk,
    )


def router(path):
    api = APIRouter(prefix="/api/editorial/settings")

    @api.get("")
    def get_settings():
        return public_settings(path)

    @api.post("")
    def save_settings(request: ProviderSettings):
        with _lock:
            config = storage.read(path) if Path(path).exists() else {}
            key = (
                request.api_key.get_secret_value().strip()
                if request.api_key
                else config.get("api_key", "")
            )
            if len(key) > 4096:
                raise ValueError("Chave inválida.")
            if request.enabled and (
                not key or not request.free_tier_confirmed or not request.text_consent
            ):
                raise ValueError(
                    "Informe a chave gratuita e confirme faturamento desativado e autorização textual."
                )
            storage.write(
                path,
                dict(
                    enabled=request.enabled,
                    provider="gemini-free",
                    model=FREE_MODELS[0],
                    api_key=key,
                    free_tier_confirmed=request.free_tier_confirmed,
                    text_consent=request.text_consent,
                ),
            )
        return public_settings(path)

    return api
