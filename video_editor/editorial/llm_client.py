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


def cache_signature(config_path):
    """Only non-secret settings participate in the response identity."""
    path=Path(config_path)
    if not path.exists():return dict(provider='unconfigured')
    try:config=storage.read(path)
    except (ValueError,OSError):return dict(provider='invalid_configuration')
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
    # Google retires free-tier models over time (404) and overloads them (503). Each model
    # in this short free list gets one try with the same key: bounded, never a paid tier.
    chain = [model] + [m for m in FREE_MODELS if m != model]
    for candidate in chain:
        try:
            from google import genai
            with genai.Client(api_key=config['api_key'],vertexai=False, http_options={'timeout': 30000,'retry_options':{'attempts':1}}) as client:
                result = client.models.generate_content(model=candidate, contents=prompt,
                    config={'response_mime_type': 'application/json', 'temperature': .2,'max_output_tokens':16384})
            return json.loads(result.text)
        except Exception as error:
            # Never stringify SDK exceptions: credentials or transcript can be present.
            if getattr(error, 'code', None) in (404, 500, 502, 503, 504) and candidate != chain[-1]:
                time.sleep(1)
                continue
            # Quota failures do not cause an unbounded wait or a paid fallback.
            raise ManualFallback('API indisponível, sem quota ou resposta inválida. Limpeza local mantida.') from None
