import json

import pytest

from video_editor.editorial import llm_client


class _Error(Exception):
    def __init__(self, code):
        super().__init__('sem detalhes')
        self.code = code


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = tmp_path / 'provider.json'
    path.write_text(json.dumps(dict(
        enabled=True, provider='gemini-free', api_key='chave-de-teste',
        free_tier_confirmed=True, model='gemini-2.5-flash',
    )), encoding='utf-8')
    monkeypatch.setattr(llm_client, '_COOLDOWN', {})
    monkeypatch.setattr(llm_client, 'QUOTA_WAIT_SECONDS', 0)
    return path


def _script(monkeypatch, outcomes):
    calls = []

    def fake(api_key, model, prompt):
        calls.append(model)
        outcome = outcomes.get(model, 'ok')
        if isinstance(outcome, list):
            outcome = outcome.pop(0) if outcome else 'ok'
        if outcome == 'ok':
            return {'model': model}
        raise _Error(outcome)

    monkeypatch.setattr(llm_client, '_call', fake)
    return calls


def test_retired_model_is_skipped_and_slow_or_busy_models_fall_through(config, monkeypatch):
    calls = _script(monkeypatch, {'gemini-3.5-flash': 504, 'gemini-flash-latest': 503})
    assert llm_client.generate(config, 'prompt') == {'model': 'gemini-flash-lite-latest'}
    assert calls == ['gemini-3.5-flash', 'gemini-flash-latest', 'gemini-flash-lite-latest']


def test_rate_limited_model_moves_to_the_next_one(config, monkeypatch):
    calls = _script(monkeypatch, {'gemini-3.5-flash': 429})
    assert llm_client.generate(config, 'prompt') == {'model': 'gemini-flash-latest'}
    assert calls == ['gemini-3.5-flash', 'gemini-flash-latest']


def test_recently_failing_models_are_tried_last(config, monkeypatch):
    calls = _script(monkeypatch, {'gemini-3.5-flash': 504, 'gemini-flash-latest': 503})
    llm_client.generate(config, 'prompt')
    calls.clear()
    llm_client.generate(config, 'prompt')
    assert calls == ['gemini-flash-lite-latest']


def test_all_rate_limited_waits_once_then_falls_back(config, monkeypatch):
    calls = _script(monkeypatch, {m: 429 for m in llm_client.FREE_MODELS})
    with pytest.raises(llm_client.ManualFallback):
        llm_client.generate(config, 'prompt')
    assert len(calls) == 2 * len(llm_client.FREE_MODELS)


def test_rate_limit_recovers_after_the_bounded_wait(config, monkeypatch):
    calls = _script(monkeypatch, {m: [429] for m in llm_client.FREE_MODELS})
    assert llm_client.generate(config, 'prompt') == {'model': 'gemini-3.5-flash'}
    assert len(calls) == len(llm_client.FREE_MODELS) + 1


def test_permanent_errors_fall_back_without_retrying(config, monkeypatch):
    calls = _script(monkeypatch, {'gemini-3.5-flash': 400})
    with pytest.raises(llm_client.ManualFallback):
        llm_client.generate(config, 'prompt')
    assert calls == ['gemini-3.5-flash']
