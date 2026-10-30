from video_editor.performance import quality, selected_batch
from video_editor import storage
from video_editor.models import PrepareConfig
from video_editor.transcription import transcribe, release_model


def test_calibration_rejects_numeric_or_negative_changes():
    ref = dict(segments=[dict(text="Ele não recebeu 100 reais")])
    assert quality(ref, ref)["eligible"]
    for text in ("Ele recebeu 100 reais", "Ele não recebeu 200 reais"):
        assert not quality(ref, dict(segments=[dict(text=text)]))["eligible"]
    assert not quality(dict(segments=[]), dict(segments=[]))["eligible"]


def test_calibration_corruption_and_other_gpu_fall_back(tmp_path, monkeypatch):
    path = tmp_path / "calibration.json"
    path.write_text("broken")
    assert selected_batch(path) == 1
    storage.write(path, dict(gpu="RTX 2060", model="small", selected_batch_size=4))
    monkeypatch.setattr("video_editor.performance.gpu", lambda: dict(name="different"))
    assert selected_batch(path) == 1
    monkeypatch.setattr("video_editor.performance.gpu", lambda: dict(name="RTX 2060"))
    assert selected_batch(path) == 4


def test_cuda_out_of_memory_reduces_batch_before_cpu(monkeypatch):
    calls = []

    class Model:
        def __init__(self, *args, **kwargs):
            self.model = object()

        def transcribe(self, *args, **kwargs):
            return iter([]), None

    class Batch:
        def __init__(self, model):
            pass

        def transcribe(self, *args, **kwargs):
            calls.append(kwargs["batch_size"])
            if kwargs["batch_size"] > 2:
                raise RuntimeError("CUDA out of memory")
            return iter([]), None

    release_model()
    monkeypatch.setattr("faster_whisper.WhisperModel", Model)
    monkeypatch.setattr("faster_whisper.BatchedInferencePipeline", Batch)
    _, actual = transcribe("unused", PrepareConfig(batch_size=8).model_dump(), {})
    assert (
        calls == [8, 4, 2] and actual["device"] == "cuda" and actual["batch_size"] == 2
    )
    release_model()


def test_refinement_can_adjust_boundaries_without_touching_next_sentence(
    tmp_path, monkeypatch
):
    import wave
    from types import SimpleNamespace as NS
    from video_editor.transcription import normalize, refine

    path = tmp_path / "audio.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\x00\x00" * 64000)
    tr = normalize(
        [
            dict(
                id="s1",
                start=0.2,
                end=1.2,
                text="Pode segui",
                words=[
                    dict(word="Pode", start=0.2, end=0.5, probability=0.1),
                    dict(word="segui", start=0.5, end=1.2, probability=0.1),
                ],
            ),
            dict(
                id="s2",
                start=2,
                end=3,
                text="Próximo assunto",
                words=[
                    dict(word="Próximo", start=2, end=2.5, probability=0.99),
                    dict(word="assunto", start=2.5, end=3, probability=0.99),
                ],
            ),
        ],
        {},
    )

    class Medium:
        def transcribe(self, *args, **kwargs):
            return iter(
                [
                    NS(
                        start=0.1,
                        end=0.9,
                        text="Pode seguir",
                        words=[
                            NS(word="Pode", start=0.1, end=0.4, probability=0.99),
                            NS(word="seguir", start=0.4, end=0.9, probability=0.99),
                        ],
                    )
                ]
            ), None

    monkeypatch.setattr(
        "faster_whisper.utils.download_model", lambda *args, **kwargs: "cached"
    )
    monkeypatch.setattr("video_editor.transcription._model", lambda *args: Medium())
    result, report = refine(path, tr, PrepareConfig().model_dump())
    assert report["replaced_ids"] == ["s1"]
    assert result["segments"][0]["text"] == "Pode seguir"
    assert result["segments"][0]["start"] < 0.2
    assert result["segments"][1] == tr["segments"][1]
