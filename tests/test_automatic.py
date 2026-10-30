import pytest
from fastapi.testclient import TestClient
from video_editor import acceptance
from video_editor.timeline import digest
from video_editor.automatic import (
    Automatic,
    classify,
    safe_bounds,
    validate_editorial,
    visual_context,
)
from video_editor.cut_policy import apply_policy
from video_editor.cuts import find_candidates
from video_editor.editing import mutate
from video_editor.export.service import Exports
from video_editor.models import EditRequest, PrepareConfig
from video_editor.server.app import create_app
from video_editor.transcription import normalize, transcribe, release_model
from video_editor.worker import Worker


def transcript(texts):
    return normalize(
        [
            dict(
                id=f"s{i + 1}",
                start=i * 3 + 0.2,
                end=i * 3 + 2.2,
                text=text,
                words=[
                    dict(
                        word=w,
                        start=i * 3 + 0.2 + j * 2 / len(text.split()),
                        end=i * 3 + 0.2 + (j + 1) * 2 / len(text.split()),
                        probability=0.99,
                    )
                    for j, w in enumerate(text.split())
                ],
            )
            for i, text in enumerate(texts)
        ],
        {},
    )


def test_editorial_nonconsecutive_segments_are_one_atomic_edit(jobs, ready):
    tr = transcript(["Primeiro trecho", "Informação exclusiva", "Terceiro trecho"])
    job = ready(segments=tr["segments"])
    request = EditRequest(
        expected_revision=0, action="cut", segment_ids=["s1", "s3"]
    ).model_dump()
    result = mutate(jobs, job["job_id"], request)
    assert len(result["edit"]["cuts"]) == 2
    assert any(
        k["source_start"] <= 3.2 and k["source_end"] >= 5.2
        for k in result["timeline"]["keeps"]
    )
    undone = mutate(jobs, job["job_id"], dict(expected_revision=1, action="undo"))
    assert undone["edit"]["cuts"] == []
    with pytest.raises(ValueError):
        mutate(
            jobs,
            job["job_id"],
            dict(expected_revision=2, action="cut", segment_ids=["missing"]),
        )
    assert jobs.read(job["job_id"], "edit.json")["revision"] == 2


def test_visual_boundaries_use_normalized_video_origin(monkeypatch):
    from types import SimpleNamespace

    seeks = []

    def decode(command, **kwargs):
        seeks.append(float(command[command.index("-ss") + 1]))
        return SimpleNamespace(returncode=0, stdout=bytes(96 * 54 * 3))

    monkeypatch.setattr("video_editor.automatic.subprocess.run", decode)
    result = visual_context(
        "source.mov", dict(duration=10, timing_contract=2, origin=1.5), 2, 4
    )
    assert seeks == pytest.approx([3.38, 5.51])
    assert result["state"] == "measured" and not result["abrupt"]


def test_visual_context_caches_repeated_frame_seeks(monkeypatch):
    from types import SimpleNamespace

    calls = []

    def decode(command, **kwargs):
        seek = float(command[command.index("-ss") + 1])
        calls.append(seek)
        return SimpleNamespace(returncode=0, stdout=bytes(96 * 54 * 3))

    monkeypatch.setattr("video_editor.automatic.subprocess.run", decode)
    cache = {}
    info = dict(duration=10, timing_contract=2, origin=1.5)
    res1 = visual_context("source.mov", info, 2, 4, cache=cache)
    assert len(calls) == 2
    assert res1["state"] == "measured"

    # Repeated query on identical boundaries uses cache (0 additional subprocess calls)
    res2 = visual_context("source.mov", info, 2, 4, cache=cache)
    assert len(calls) == 2
    assert res2 == res1

    # Shifted end boundary decodes only the new right boundary (1 additional subprocess call)
    res3 = visual_context("source.mov", info, 2, 5, cache=cache)
    assert len(calls) == 3
    assert res3["state"] == "measured"


def test_aligned_cut_protects_replacement_evidence_in_same_sentence():
    tr = normalize(
        [
            dict(
                id="s1",
                start=0,
                end=2,
                text="Eu eu continuo",
                words=[
                    dict(word="Eu", start=0, end=0.99, probability=0.99),
                    dict(word="eu", start=0.995, end=1.5, probability=0.99),
                    dict(word="continuo", start=1.5, end=2, probability=0.99),
                ],
            )
        ],
        {},
    )
    words = tr["segments"][0]["words"]
    proposal = dict(
        start=0,
        end=0.99,
        evidence=[w["id"] for w in words[:2]],
        removed_word_ids=[words[0]["id"]],
    )
    assert safe_bounds(proposal, tr, 2, 24) is None


def test_frame_alignment_cannot_invade_next_word():
    tr = normalize(
        [
            dict(
                id="s1",
                start=0.2,
                end=0.99,
                text="Vou repetir",
                words=[
                    dict(word="Vou", start=0.2, end=0.45, probability=0.99),
                    dict(word="repetir", start=0.5, end=0.99, probability=0.99),
                ],
            ),
            dict(
                id="s2",
                start=1.051,
                end=1.6,
                text="Conteúdo",
                words=[dict(word="Conteúdo", start=1.051, end=1.6, probability=0.99)],
            ),
        ],
        {},
    )
    assert not any(c["auto"] for c in apply_policy(tr, find_candidates(tr), 3, 30))
    assert safe_bounds(dict(start=0.14, end=1.05, evidence=["s1"]), tr, 3, 30) is None


@pytest.mark.parametrize(
    "phrase", ["Isso é sério!", "Você entendeu?", "Pense nisso...", "São 100 pessoas."]
)
def test_expressive_pause_is_preserved(phrase):
    tr = transcript([phrase, "Agora vamos discutir outro ponto importante."])
    candidate = dict(
        id="pause", kind="silence", auto=True, start=2.3, end=3.1, evidence={}
    )
    result = classify(tr, [candidate], 8, 30)
    assert not result[0]["auto"]
    assert result[0]["evaluation"]["expressive_pause"]


def test_quiet_pause_obeys_protected_intervals():
    tr = transcript(
        [
            "Essa frase oferece um contexto bastante completo.",
            "Esta frase apresenta nosso próximo argumento.",
        ]
    )
    candidate = dict(
        id="pause", kind="silence", auto=True, start=2.4, end=3.0, evidence={}
    )
    assert classify(tr, [candidate], 8, 30)[0]["auto"]
    assert not classify(tr, [candidate], 8, 30, protected=[dict(start=2.6, end=2.8)])[
        0
    ]["auto"]


def test_short_pause_at_slow_cadence_is_kept_without_invalid_bounds():
    tr = transcript(
        ["Esta é uma frase bastante completa.", "Esta outra frase conclui o argumento."]
    )
    proposal = dict(
        id="adaptive",
        kind="adaptive_silence",
        start=2.525,
        end=2.875,
        auto=False,
        evidence={},
    )
    result = classify(tr, [proposal], 8, 30)[0]
    assert result["evaluation"]["rhythm_keep"]
    assert result["start"] < result["end"] and not result["auto"]


def test_adaptive_noise_proposals_preserve_music_and_remain_gated(tmp_path):
    import wave
    import numpy as np
    from video_editor.activity import adaptive_candidates

    tr = normalize(
        [
            dict(
                id="s1",
                start=0,
                end=0.5,
                text="Uma frase completa antes desta pausa.",
                words=[dict(word="frase", start=0, end=0.5)],
            ),
            dict(
                id="s2",
                start=5.5,
                end=6,
                text="Outra frase completa depois desta pausa.",
                words=[dict(word="frase", start=5.5, end=6)],
            ),
        ],
        {},
    )
    rng = np.random.default_rng(42)
    for kind in ("noise", "music"):
        values = (
            rng.normal(0, 25, 96000)
            if kind == "noise"
            else 160 * np.sin(2 * np.pi * 440 * np.arange(96000) / 16000)
        )
        edge = 20000 * np.sin(2 * np.pi * 440 * np.arange(8000) / 16000)
        values[:8000] = edge
        values[-8000:] = edge
        path = tmp_path / (kind + ".wav")
        with wave.open(str(path), "wb") as handle:
            handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            handle.writeframes(values.astype("<i2").tobytes())
        proposals = adaptive_candidates(tr, path, path, 6)
        assert proposals
        assert proposals[0]["evidence"]["stationary_noise"] == (kind == "noise")
        assert not any(c["auto"] for c in classify(tr, proposals, 6, 30))


@pytest.mark.parametrize(
    "texts",
    [
        ["Talvez...", "Podemos apresentar um argumento mais claro."],
        ["O valor é 100 reais", "Corrigindo o valor é 200 reais"],
        ["Essa ideia precisa acabar agora.", "Essa ideia precisa acabar agora."],
    ],
)
def test_new_classes_remain_review_only(texts):
    tr = transcript(texts)
    candidates = find_candidates(tr)
    assert candidates
    assert not any(c["auto"] for c in classify(tr, candidates, 10, 30))


def test_editorial_cannot_launder_changed_number():
    tr = transcript(["O total foi 100 reais", "O total foi 200 reais"])
    candidate = dict(id="c1", start=0.2, end=2.2, evidence=["s1", "s2"])
    result = dict(
        decisions=[
            dict(
                suggestion_id="c1",
                action="remove",
                replacement_segment_ids=["s2"],
                information_loss=False,
                coherent_join=True,
                reason="Mesma ideia",
            )
        ]
    )
    assert not validate_editorial(result, [candidate], tr)[0]["replacement_proven"]
    result["decisions"][0]["replacement_segment_ids"] = ["invented"]
    with pytest.raises(ValueError):
        validate_editorial(result, [candidate], tr)


def test_intra_sentence_replacement_uses_actual_word_ids():
    tr = transcript(["Eu acho eu acho que podemos seguir"])
    candidates = find_candidates(tr)
    candidate = next(c for c in candidates if c.get("removed_word_ids"))
    result = dict(
        decisions=[
            dict(
                suggestion_id=candidate["id"],
                action="remove",
                replacement_segment_ids=[],
                replacement_word_ids=candidate["replacement_word_ids"],
                information_loss=False,
                coherent_join=True,
                reason="Reinício",
            )
        ]
    )
    assert validate_editorial(result, [candidate], tr)[0]["replacement_proven"]
    result["decisions"][0]["replacement_word_ids"] = ["invented"]
    with pytest.raises(ValueError):
        validate_editorial(result, [candidate], tr)


def test_explicit_correction_requires_retraction_and_confident_replacement():
    tr = transcript(["O valor é 100 reais", "Corrigindo o valor é 200 reais"])
    candidate = next(c for c in find_candidates(tr) if c["kind"] == "correction")
    result = dict(
        decisions=[
            dict(
                suggestion_id=candidate["id"],
                action="remove",
                replacement_segment_ids=["s2"],
                information_loss=False,
                coherent_join=True,
                reason="Correção explícita do locutor",
            )
        ]
    )
    assert validate_editorial(result, [candidate], tr)[0]["replacement_proven"]
    tr["segments"][1]["words"][0]["probability"] = 0.3
    assert not validate_editorial(result, [candidate], tr)[0]["replacement_proven"]


def test_automatic_creation_api_finishes_without_cloud(jobs, ready):
    source_job = ready()
    source = source_job["source"]["path"]
    before = open(source, "rb").read()
    renders = []

    class Pipeline:
        def prepare(self, jid):
            current = jobs.info(jid)
            tr = transcript(
                [
                    "Esse trecho contém nossa primeira frase completa.",
                    "Esse outro trecho contém a conclusão da gravação.",
                ]
            )
            tr["source_hash"] = digest(current["source"])
            jobs.write(jid, "transcript.json", tr)
            jobs.write(jid, "suggestions.json", [])
            jobs.write(
                jid,
                "edit.json",
                dict(
                    revision=0,
                    transcript_hash=digest(tr),
                    cuts=[],
                    markers=[],
                    protected=[],
                    undo=[],
                    history=[],
                ),
            )
            return jobs.update(
                jid, state="ready", stage="ready", media=dict(duration=20, fps=30)
            )

    def renderer(snapshot, path, root):
        renders.append(snapshot)
        path.write_bytes(b"isolated render")
        return {}

    app = create_app(
        jobs.root,
        jobs.source_dir,
        pipeline=Pipeline(),
        renderer=renderer,
        recover=False,
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/automatic-jobs",
            json=dict(source_path=source, automatic=dict(send_text=False, export=True)),
        )
        assert response.status_code == 202
        jid = response.json()["job_id"]
        app.state.worker.queue.join()
        state = client.get("/api/jobs/" + jid + "/automatic").json()
        assert state["state"] == "complete" and len(renders) == 1
        assert (
            client.get(
                "/api/jobs/" + jid + "/exports/" + state["render_id"] + "/subtitles.srt"
            ).status_code
            == 200
        )
        assert open(source, "rb").read() == before


def test_audition_is_read_only_and_uses_backend_frame_map(jobs, ready):
    job = ready()
    jid = job["job_id"]
    jobs.write(
        jid,
        "suggestions.json",
        [
            dict(
                id="pause",
                start=3.01,
                end=7.99,
                kind="silence",
                auto=False,
                reason="Pausa",
            )
        ],
    )
    app = create_app(jobs.root, jobs.source_dir, recover=False)
    with TestClient(app) as client:
        before = {
            str(p): p.read_bytes()
            for p in jobs.root.rglob("*")
            if p.is_file() and p.name != ".job.lock"
        }
        response = client.get("/api/jobs/" + jid + "/audition?suggestion_id=pause")
        assert response.status_code == 200
        assert response.json()["timeline"]["cuts"] == [[3, 8]]
        assert response.json()["revision"] == 0
        assert before == {
            str(p): p.read_bytes()
            for p in jobs.root.rglob("*")
            if p.is_file() and p.name != ".job.lock"
        }


def test_asr_model_is_reused_and_evicted_on_configuration_change(monkeypatch):
    calls = []

    class Model:
        def __init__(self, size, **kwargs):
            calls.append(size)

        def transcribe(self, *args, **kwargs):
            return iter([]), None

    release_model()
    monkeypatch.setattr("faster_whisper.WhisperModel", Model)
    config = PrepareConfig(device="cpu", model="small").model_dump()
    _, first = transcribe("unused", config, {})
    _, second = transcribe("unused", config, {})
    transcribe("unused", dict(config, model="base"), {})
    assert (
        calls == ["small", "base"]
        and not first["model_reused"]
        and second["model_reused"]
    )
    release_model()


def test_automatic_exports_once_and_human_restore_is_not_reapplied(
    jobs, ready, monkeypatch
):
    job = ready()
    jid = job["job_id"]
    captured = []
    jobs.write(
        jid,
        "suggestions.json",
        [
            dict(
                id="p",
                kind="silence",
                start=4,
                end=8,
                auto=True,
                evidence={},
                reason="Pausa",
            )
        ],
    )
    edit = jobs.read(jid, "edit.json")
    edit["cuts"] = [dict(start=4, end=8, origin="mechanical", suggestion_id="p")]
    jobs.write(jid, "edit.json", edit)
    monkeypatch.setattr(
        "video_editor.automatic.visual_context",
        lambda *args: dict(state="measured", abrupt=False),
    )

    def renderer(snapshot, path, root):
        captured.append(snapshot)
        path.write_bytes(b"export")
        return dict(actual_codec="test")

    worker = Worker()
    exports = Exports(jobs, worker, renderer)
    service = Automatic(
        jobs, worker, None, exports, jobs.root / "missing-provider.json"
    )
    service.request(jid, dict(send_text=False, export=True), 0)
    worker.queue.join()
    status = service.status(jid)
    assert status["state"] == "complete" and len(captured) == 1
    assert jobs.path(jid, f"exports/{status['render_id']}/subtitles.srt").exists()
    revision = jobs.read(jid, "edit.json")["revision"]
    service.request(jid, dict(send_text=False, export=True), revision)
    worker.queue.join()
    assert service.status(jid)["reused_export"] is True
    assert len(captured) == 1
    mutate(
        jobs, jid, dict(expected_revision=revision, action="restore", suggestion_id="p")
    )
    revision += 1
    service.request(jid, dict(send_text=False, export=False), revision)
    worker.queue.join()
    worker.close()
    assert jobs.read(jid, "edit.json")["cuts"] == []


def test_human_edit_during_analysis_wins(jobs, ready, monkeypatch):
    job = ready()
    jid = job["job_id"]
    jobs.write(
        jid,
        "suggestions.json",
        [
            dict(
                id="reset",
                kind="reset",
                start=4,
                end=8,
                auto=True,
                evidence=[],
                reason="Comando",
            )
        ],
    )

    def visual(*args):
        mutate(
            jobs,
            jid,
            dict(expected_revision=0, action="marker", source_time=2, label="Humano"),
        )
        return dict(state="measured", abrupt=False)

    monkeypatch.setattr("video_editor.automatic.visual_context", visual)
    worker = Worker()
    service = Automatic(jobs, worker, None, Exports(jobs, worker), jobs.root / "none")
    service.request(jid, dict(send_text=False, export=False), 0)
    worker.queue.join()
    worker.close()
    assert service.status(jid)["state"] == "failed"
    assert jobs.read(jid, "edit.json")["markers"][0]["label"] == "Humano"
    assert jobs.read(jid, "edit.json")["cuts"] == []


def test_partial_human_restore_is_not_resurrected_by_automatic_reanalysis(
    jobs, ready, monkeypatch
):
    job = ready()
    jid = job["job_id"]
    suggestion = dict(
        id="reset",
        kind="reset",
        start=0.4,
        end=2.2,
        auto=True,
        evidence=["s1"],
        reason="Comando",
    )
    jobs.write(jid, "suggestions.json", [suggestion])
    edit = jobs.read(jid, "edit.json")
    edit["cuts"] = [dict(start=0.4, end=2.2, origin="automatic", suggestion_id="reset")]
    jobs.write(jid, "edit.json", edit)
    result = mutate(
        jobs, jid, dict(expected_revision=0, action="restore", start=1, end=2)
    )
    expected = result["edit"]["cuts"]
    monkeypatch.setattr(
        "video_editor.automatic.visual_context",
        lambda *args: dict(state="measured", abrupt=False),
    )
    worker = Worker()
    service = Automatic(jobs, worker, None, Exports(jobs, worker), jobs.root / "none")
    service.request(jid, dict(send_text=False, export=False), 1)
    worker.queue.join()
    worker.close()
    assert service.status(jid)["state"] == "complete"
    assert jobs.read(jid, "edit.json")["cuts"] == expected


def test_cloud_failures_preserve_retake_and_complete(jobs, ready, monkeypatch):
    job = ready(
        segments=transcript(
            ["O argumento é relevante", "O argumento é relevante para todos"]
        )["segments"]
    )
    jid = job["job_id"]
    jobs.write(
        jid, "suggestions.json", find_candidates(jobs.read(jid, "transcript.json"))
    )
    monkeypatch.setattr(
        "video_editor.editorial.llm_client.generate",
        lambda *args: {"decisions": [{"suggestion_id": "hallucinated"}]},
    )
    worker = Worker()
    service = Automatic(jobs, worker, None, Exports(jobs, worker), jobs.root / "none")
    service.request(jid, dict(send_text=True, export=False), 0)
    worker.queue.join()
    worker.close()
    assert service.status(jid)["state"] == "complete"
    assert service.status(jid)["report"]["provider"] == "unavailable_or_invalid"
    assert jobs.read(jid, "edit.json")["cuts"] == []


def test_validated_class_requires_actual_replacement_before_cut(
    jobs, ready, monkeypatch
):
    job = ready(
        segments=transcript(
            [
                "Essa ideia é relevante para todos",
                "Essa ideia é relevante para todos hoje",
            ]
        )["segments"]
    )
    jid = job["job_id"]
    candidates = find_candidates(jobs.read(jid, "transcript.json"))
    jobs.write(jid, "suggestions.json", candidates)
    candidate = next(c for c in candidates if c["id"].startswith("retake-"))
    monkeypatch.setattr(
        "video_editor.acceptance.gate",
        lambda jobs: dict(enabled=True, state="validated_five_videos"),
    )
    monkeypatch.setattr(
        "video_editor.automatic.visual_context",
        lambda *args: dict(state="measured", abrupt=False),
    )
    monkeypatch.setattr(
        "video_editor.editorial.llm_client.generate",
        lambda *args: dict(
            decisions=[
                dict(
                    suggestion_id=candidate["id"],
                    action="remove",
                    replacement_segment_ids=["s2"],
                    information_loss=False,
                    coherent_join=True,
                    reason="A segunda tomada contém a primeira inteira.",
                )
            ]
        ),
    )
    worker = Worker()
    service = Automatic(jobs, worker, None, Exports(jobs, worker), jobs.root / "none")
    service.request(jid, dict(send_text=True, export=False), 0)
    worker.queue.join()
    worker.close()
    assert service.status(jid)["state"] == "complete"
    assert jobs.read(jid, "edit.json")["cuts"][0]["suggestion_id"] == candidate["id"]
    assert jobs.read(jid, "edit.json")["cuts"][0]["end"] < 3.2


def test_api_requires_boolean_text_consent_and_reads_are_pure(jobs, ready):
    job = ready()
    app = create_app(jobs.root, jobs.source_dir, recover=False)
    with TestClient(app) as client:
        for consent in ("true", 1):
            response = client.post(
                "/api/automatic-jobs",
                json=dict(
                    source_path=job["source"]["path"], automatic=dict(send_text=consent)
                ),
            )
            assert response.status_code == 422
        prefix = "/api/jobs/" + job["job_id"]
        before = {
            str(p): p.read_bytes()
            for p in jobs.root.rglob("*")
            if p.is_file() and p.name != ".job.lock"
        }
        assert client.get(prefix + "/automatic").json()["state"] == "not_requested"
        assert before == {
            str(p): p.read_bytes()
            for p in jobs.root.rglob("*")
            if p.is_file() and p.name != ".job.lock"
        }


def test_five_approved_uncut_videos_do_not_unlock_classes(jobs, ready):
    for i in range(5):
        job = ready(name=f"new-{i}.mp4")
        jid = job["job_id"]
        worker = Worker()
        exports = Exports(
            jobs, worker, lambda snapshot, path, root: (path.write_bytes(b"mp4"), {})[1]
        )
        exports.request(jid, 0)
        worker.queue.join()
        worker.close()
        jobs.write(
            jid,
            "automatic_report.json",
            dict(revision=0, suggestions=[], policy_hash=acceptance.policy_hash()),
        )
        acceptance.record(
            jobs,
            jid,
            dict(
                expected_revision=0,
                labels={},
                watched_complete=True,
                approved=True,
                severe_errors=0,
                naturalness=4,
                missed_errors=1,
                correction_minutes=0.5,
            ),
        )
        assert not acceptance.gate(jobs)["enabled"]
    mutate(jobs, jid, dict(expected_revision=0, action="marker", source_time=1))
    assert not acceptance.gate(jobs)["enabled"]


def test_recovery_and_retry_do_not_repeat_applied_cuts(jobs, ready, monkeypatch):
    job = ready()
    jid = job["job_id"]
    jobs.write(
        jid,
        "automatic.json",
        dict(
            state="exporting",
            options=dict(send_text=False, export=True),
            applied_revision=0,
        ),
    )
    jobs.write(jid, "automatic_report.json", dict(revision=0, suggestions=[]))
    captured = []

    def renderer(snapshot, path, root):
        captured.append(snapshot)
        path.write_bytes(b"test")
        return {}

    app = create_app(jobs.root, jobs.source_dir, renderer=renderer)
    with TestClient(app) as client:
        prefix = "/api/jobs/" + jid
        assert client.get(prefix + "/automatic").json()["state"] == "interrupted"
        assert (
            client.post(
                prefix + "/automatic", json={"expected_revision": 0}
            ).status_code
            == 202
        )
        app.state.worker.queue.join()
        assert client.get(prefix + "/automatic").json()["state"] == "complete"
        assert jobs.read(jid, "edit.json")["revision"] == 0 and len(captured) == 1
