from pathlib import Path

from video_editor.learning import acceptance


def test_policy_hash_reads_every_legacy_source(monkeypatch):
    root = Path(acceptance.__file__).resolve().parent.parent
    seen = set()
    original = Path.read_text

    def read(path, *args, **kwargs):
        try:
            seen.add(Path(path).resolve().relative_to(root).as_posix())
        except ValueError:
            pass
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', read)
    acceptance.policy_hash()
    expected = {
        'analysis/contextual.py', 'analysis/noise_profile.py', 'analysis/pacing.py',
        'analysis/takes.py', 'learning/acceptance.py', 'learning/activity.py',
        'learning/automatic_evaluation.py', 'learning/channel_profile.py',
        'models.py', 'cuts.py', 'cut_policy.py', 'transcription.py', 'timeline.py',
        'pipeline.py', 'media.py', 'export/renderer.py', 'export/service.py',
        'editing.py', 'editorial/llm_client.py',
        'automatic/__init__.py', 'automatic/bounds.py', 'automatic/classify.py',
        'automatic/constants.py', 'automatic/context.py', 'automatic/editorial.py',
        'automatic/runner.py', 'automatic/runner_apply.py', 'automatic/runner_context.py',
        'automatic/runner_run.py', 'automatic/runner_validation.py', 'automatic/voice.py',
    }
    assert expected <= seen
