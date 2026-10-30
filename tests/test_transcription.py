import pytest
from video_editor.transcription import transcribe, release_model
from video_editor.models import PrepareConfig


def test_default_model_is_turbo_and_skips_small_only_refinement(monkeypatch):
    assert PrepareConfig().model == 'large-v3-turbo'
    calls = []
    class Model:
        def __init__(self, size, **kwargs):
            calls.append(size)
        def transcribe(self, *args, **kwargs):
            return iter([]), None
    release_model()
    monkeypatch.setattr('faster_whisper.WhisperModel', Model)
    config = PrepareConfig(model='large-v3-turbo', device='cpu', refine_uncertain=True).model_dump()
    _, actual = transcribe('unused.wav', config, {})
    assert calls == ['large-v3-turbo']
    assert actual['model'] == 'large-v3-turbo'
    assert 'refinement' not in actual
    release_model()


def test_gpu_fallback_only_for_device_error_and_cache_only(monkeypatch):
    calls=[]
    class Model:
        def __init__(self,size,**kwargs):
            calls.append(kwargs)
            assert kwargs['local_files_only'] is True
            if kwargs['device']=='cuda':
                raise RuntimeError('CUDA unavailable')
        def transcribe(self,path,**kwargs):
            return iter([]),None
    monkeypatch.setattr('faster_whisper.WhisperModel',Model)
    _,actual=transcribe('unused.wav',PrepareConfig().model_dump(),{})
    assert actual['device']=='cpu'
    assert actual['compute_type']=='int8'
    assert len(calls)==2


def test_decode_failure_is_not_disguised_as_gpu_fallback(monkeypatch):
    calls=[]
    class Model:
        def __init__(self,*args,**kwargs):
            calls.append(kwargs)
        def transcribe(self,*args,**kwargs):
            raise RuntimeError('invalid audio data')
    monkeypatch.setattr('faster_whisper.WhisperModel',Model)
    with pytest.raises(RuntimeError,match='invalid audio'):
        transcribe('unused.wav',PrepareConfig().model_dump(),{})
    assert len(calls)==1
