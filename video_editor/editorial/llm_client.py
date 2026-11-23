"""Exactly one optional provider. No environmental key autodetection or paid fallback."""
import json
import time
from pathlib import Path
from video_editor import storage


class ManualFallback(ValueError):
    pass


# Free-tier text models, newest first (checked against the API on 2026-10-27).
FREE_MODELS = ('gemini-3.5-flash', 'gemini-flash-latest', 'gemini-flash-lite-latest')
# Saved configurations may still name these; they fall through to FREE_MODELS.
RETIRED_MODELS = ('gemini-2.5-flash', 'gemini-2.5-flash-lite')
TRANSIENT = (404, 429, 500, 502, 503, 504)
# Long editorial prompts take 30-60 s on the flash model; 30 s cut them off with a 504.
REQUEST_TIMEOUT_MS = 120_000
COOLDOWN_SECONDS = 300
QUOTA_WAIT_SECONDS = 30
_COOLDOWN = {}


def cache_signature(config_path):
    """Only non-secret settings participate in the response identity."""
    path=Path(config_path)
    if not path.exists():
        return dict(provider='unconfigured')
    try:
        config=storage.read(path)
    except (ValueError,OSError):
        return dict(provider='invalid_configuration')
    return {k:config.get(k) for k in ('provider','model','enabled','free_tier_confirmed')}


def generate(config_path, prompt):
    path = Path(config_path)
    if not path.exists():
        raise ManualFallback('Provider não configurado. Copie o prompt local.')
    config = storage.read(path)
    if config.get('enabled') is not True or config.get('provider') != 'gemini-free' or not config.get('api_key'):
        raise ManualFallback('Provider desabilitado. Copie o prompt local.')
    if config.get('free_tier_confirmed') is not True:
        raise ManualFallback('Confirme uma chave sem faturamento no arquivo de configuração.')
    if not isinstance(prompt, str) or len(prompt) > 500000:
        raise ManualFallback('Pacote textual muito grande; use exportação manual.')
    # Model is explicit: no provider/model selection based on ambient environment.
    model = config.get('model')
    if model not in FREE_MODELS + RETIRED_MODELS:
        raise ManualFallback('Configure um modelo com tier gratuito suportado.')
    # Google retires free-tier models over time (404), overloads them (503), lets big
    # prompts run past the deadline (504) and rate-limits each model separately (429).
    # Each model in this short free list gets one try with the same key, a model that just
    # failed is skipped for a few minutes, and one bounded wait follows when every model
    # was rate-limited: never an unbounded wait, never a paid tier.
    chain = [m for m in [model] + list(FREE_MODELS) if m not in RETIRED_MODELS]
    chain = list(dict.fromkeys(chain))
    healthy = [m for m in chain if _COOLDOWN.get(m, 0) <= time.monotonic()]
    order = healthy + [m for m in chain if m not in healthy]
    for attempt in range(2):
        limited = False
        for candidate in order:
            try:
                return _call(config['api_key'], candidate, prompt)
            except _BadJson:
                raise ManualFallback(
                    'Resposta inválida do provider. Limpeza local mantida.') from None
            except Exception as error:
                # Never stringify SDK exceptions: credentials or transcript can be present.
                code = getattr(error, 'code', None)
                if code in TRANSIENT:
                    _COOLDOWN[candidate] = time.monotonic() + COOLDOWN_SECONDS
                    limited = limited or code == 429
                    continue
                raise ManualFallback(
                    'API indisponível, sem quota ou resposta inválida. Limpeza local mantida.') from None
        if not limited or attempt:
            break
        time.sleep(QUOTA_WAIT_SECONDS)
    # Quota failures do not cause an unbounded wait or a paid fallback.
    raise ManualFallback('API indisponível, sem quota ou resposta inválida. Limpeza local mantida.')


class _BadJson(ValueError):
    pass


def _call(api_key, model, prompt):
    from google import genai
    with genai.Client(api_key=api_key, vertexai=False,
                      http_options={'timeout': REQUEST_TIMEOUT_MS, 'retry_options': {'attempts': 1}}) as client:
        result = client.models.generate_content(model=model, contents=prompt,
            config={'response_mime_type': 'application/json', 'temperature': .2, 'max_output_tokens': 16384})
    try:
        return json.loads(result.text)
    except (TypeError, ValueError):
        raise _BadJson() from None
