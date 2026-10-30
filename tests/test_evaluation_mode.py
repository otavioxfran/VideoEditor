import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from video_editor import acceptance
from video_editor.automatic import Automatic, validate_editorial
from video_editor.automatic_evaluation import clone_prepared, local_decisions
from video_editor.cuts import find_candidates
from video_editor.editing import mutate
from video_editor.export.service import Exports
from video_editor.models import AutomaticOptions
from video_editor.noise_profile import analyze
from video_editor.safety import source_identity
from video_editor.server.app import create_app
from video_editor.timeline import digest
from video_editor.transcription import normalize
from video_editor.worker import Worker


def write_wav(path, audio):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setparams(
            (
                audio.shape[1] if audio.ndim == 2 else 1,
                2,
                16000,
                0,
                "NONE",
                "not compressed",
            )
        )
        handle.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())


@pytest.fixture
def acoustic(monkeypatch, tmp_path):
    monkeypatch.setattr("faster_whisper.vad.get_speech_timestamps", lambda *args: [])
    rng = np.random.default_rng(741)
    audio = rng.normal(0, 0.001, 160000).astype(np.float32)
    path = tmp_path / "audio.wav"
    tr = normalize(
        [
            dict(
                start=0.2,
                end=1,
                text="Uma frase completa termina aqui.",
                words=[dict(word="aqui.", start=0.2, end=1)],
            ),
            dict(
                start=8,
                end=9,
                text="A próxima frase começa depois.",
                words=[dict(word="depois.", start=8, end=9)],
            ),
        ],
        {},
    )
    proposal = dict(
        id="p",
        kind="adaptive_silence",
        start=2,
        end=7.5,
        auto=False,
        evidence=dict(native_clear=True, vad_clear=True),
        reason="teste",
    )
    return audio, path, tr, proposal


def test_relative_profile_accepts_room_noise_and_low_hum(acoustic):
    audio, path, tr, proposal = acoustic
    audio += 0.001 * np.sin(2 * np.pi * 50 * np.arange(len(audio)) / 16000)
    write_wav(path, audio)
    proposed, profile = analyze(tr, path, [proposal])
    assert profile["state"] == "measured" and profile["profiled_without_words"]
    assert any(s["evidence"].get("background_match") for s in proposed)


@pytest.mark.parametrize("foreground", ["breath", "click", "music"])
def test_proposals_split_around_foreground_and_keep_breathing(acoustic, foreground):
    audio, path, tr, proposal = acoustic
    start, end = 3.4, 4.2
    left, right = int(start * 16000), int(end * 16000)
    if foreground == "breath":
        audio[left:right] += np.random.default_rng(18).normal(0, 0.018, right - left)
    elif foreground == "click":
        audio[left : left + 32] += 0.25
        end = start + 0.002
    else:
        audio[left:right] += 0.002 * np.sin(
            2 * np.pi * 440 * np.arange(right - left) / 16000
        )
    write_wav(path, audio)
    proposed, _ = analyze(tr, path, [proposal])
    cuts = [s for s in proposed if s["evidence"].get("background_match")]
    assert cuts
    assert all(not (s["start"] < end + 0.15 and s["end"] > start - 0.15) for s in cuts)


def test_quiet_music_never_becomes_a_background_reference(acoustic):
    audio, path, tr, proposal = acoustic
    audio[:] = 0.0005 * np.sin(2 * np.pi * 440 * np.arange(len(audio)) / 16000)
    write_wav(path, audio)
    proposed, profile = analyze(tr, path, [proposal])
    assert profile["state"] == "insufficient_background"
    assert not any(s["evidence"].get("background_match") for s in proposed)


def test_native_channels_prevent_phase_cancellation_cut(acoustic):
    audio, path, tr, proposal = acoustic
    stereo = np.stack((audio, -audio), axis=1)
    start, end = 3.4, 4.2
    tone = 0.002 * np.sin(
        2 * np.pi * 440 * np.arange(int((end - start) * 16000)) / 16000
    )
    count = len(tone)
    stereo[int(start * 16000) : int(start * 16000) + count, 0] += tone
    stereo[int(start * 16000) : int(start * 16000) + count, 1] -= tone
    native = path.with_name("stereo.wav")
    write_wav(native, stereo)
    write_wav(path, stereo.mean(axis=1))
    proposed, profile = analyze(tr, path, [proposal], native)
    assert profile["channels"] == 2
    assert all(
        not (s["start"] < end and s["end"] > start)
        for s in proposed
        if s["evidence"].get("background_match")
    )


def _prepared(jobs, ready):
    segments = []
    for start, text in [
        (1, "Esta primeira frase está totalmente completa."),
        (15, "Essa outra frase também está completa."),
    ]:
        parts = text.split()
        segments.append(
            dict(
                start=start,
                end=start + 1,
                text=text,
                words=[
                    dict(
                        word=w,
                        start=start + i / len(parts),
                        end=start + (i + 1) / len(parts),
                    )
                    for i, w in enumerate(parts)
                ],
            )
        )
    job = ready(segments=segments)
    jid = job["job_id"]
    audio = np.random.default_rng(43).normal(0, 0.001, 320000).astype(np.float32)
    write_wav(jobs.path(jid, "audio/render_pcm.wav"), audio)
    write_wav(jobs.path(jid, "audio/extracted_16k.wav"), audio)
    pcm = source_identity(jobs.path(jid, "audio/render_pcm.wav"))
    jobs.write(
        jid,
        "audio/checkpoint.json",
        dict(
            contract=2,
            source_hash=digest(job["source"]),
            render_audio=pcm,
            mono_audio=source_identity(jobs.path(jid, "audio/extracted_16k.wav")),
        ),
    )
    jobs.write(jid, "audio/activity.json", [])
    return jobs.update(jid, render_audio=pcm)


def test_evaluation_api_applies_real_candidate_in_separate_job(
    jobs, ready, monkeypatch
):
    parent = _prepared(jobs, ready)
    jid = parent["job_id"]
    before = {
        str(p): p.read_bytes()
        for p in jobs.directory(jid).rglob("*")
        if p.is_file() and p.name != ".job.lock"
    }
    candidate = dict(
        id="p",
        kind="adaptive_silence",
        start=4,
        end=8,
        auto=False,
        reason="pausa",
        evidence=dict(native_clear=True, vad_clear=True),
    )
    monkeypatch.setattr(
        "video_editor.activity.adaptive_for_job", lambda *args: [candidate]
    )
    monkeypatch.setattr(
        "video_editor.automatic.visual_context",
        lambda *args: dict(state="measured", abrupt=False),
    )
    monkeypatch.setattr("faster_whisper.vad.get_speech_timestamps", lambda *args: [])
    monkeypatch.setattr(
        "video_editor.editorial.llm_client.generate",
        lambda *args: pytest.fail("Texto não pode sair do PC"),
    )
    renders = []

    def renderer(snapshot, path, root):
        renders.append(snapshot)
        path.write_bytes(b"isolated mp4")
        return {}

    app = create_app(jobs.root, jobs.source_dir, renderer=renderer, recover=False)
    with TestClient(app) as client:
        response = client.post(
            f"/api/jobs/{jid}/automatic/evaluation", json=dict(expected_revision=0)
        )
        assert response.status_code == 202, response.text
        child = response.json()
        cid = child["job_id"]
        assert cid != jid and child["editing_mode"] == "evaluation"
        app.state.worker.queue.join()
        state = client.get(f"/api/jobs/{cid}/automatic").json()
        assert state["state"] == "complete", state
        assert (
            state["report"]["result"] == "cuts_applied"
            and state["report"]["applied_cuts"]
        )
        assert (
            state["report"]["provider"] == "disabled"
            and not state["acceptance"]["enabled"]
        )
        assert (
            renders[0]["mode"] == "evaluation"
            and renders[0]["timeline"]["duration"] < 20
        )
        assert (
            Exports(jobs, Worker()).get(cid, state["render_id"])["mode"] == "evaluation"
        )
        assert before == {
            str(p): p.read_bytes()
            for p in jobs.directory(jid).rglob("*")
            if p.is_file() and p.name != ".job.lock"
        }
        assert jobs.info(cid)["render_audio"]["path"] != parent["render_audio"]["path"]
        edit = jobs.read(cid, "edit.json")
        assert (
            mutate(jobs, cid, dict(expected_revision=edit["revision"], action="undo"))[
                "edit"
            ]["cuts"]
            == []
        )


def test_evaluation_rejects_changed_revision_and_accepts_explicit_text(jobs, ready):
    parent = _prepared(jobs, ready)
    with pytest.raises(ValueError, match="edição mudou"):
        clone_prepared(jobs, parent["job_id"], 3)
    child = clone_prepared(jobs, parent["job_id"], 0)
    class Queue:
        def submit(self,*args):
            pass
    service = Automatic(jobs, Queue(), None, None, jobs.root / "none")
    value=service.request(child["job_id"], dict(mode="evaluation", send_text=True), 0)
    assert value['options']['send_text'] is True
    assert AutomaticOptions().mode == "standard"


def test_old_checkpoint_rebuilds_mono_only_in_child(jobs, ready):
    parent = _prepared(jobs, ready)
    jid = parent["job_id"]
    checkpoint = jobs.read(jid, "audio/checkpoint.json")
    checkpoint.pop("mono_audio")
    jobs.write(jid, "audio/checkpoint.json", checkpoint)
    old = jobs.path(jid, "audio/extracted_16k.wav")
    old.write_bytes(b"Old unverified derivative is never copied")
    child = clone_prepared(jobs, jid, 0)
    assert old.read_bytes() == b"Old unverified derivative is never copied"
    assert child["timings"]["mono_rebuilt_from_pcm"] is True
    with wave.open(
        str(jobs.path(child["job_id"], "audio/extracted_16k.wav"))
    ) as handle:
        assert handle.getframerate() == 16000 and handle.getnframes() == 320000


def _restart(text, tail, punctuation="..."):
    return normalize(
        [
            dict(
                id="s1",
                start=0.2,
                end=2,
                text=text + punctuation,
                words=[
                    dict(
                        word=w,
                        start=0.2 + i * 1.8 / len(text.split()),
                        end=0.2 + (i + 1) * 1.8 / len(text.split()),
                        probability=0.99,
                    )
                    for i, w in enumerate(text.split())
                ],
            ),
            dict(
                id="s2",
                start=2.5,
                end=5,
                text=tail,
                words=[
                    dict(
                        word=w,
                        start=2.5 + i * 2.5 / len(tail.split()),
                        end=2.5 + (i + 1) * 2.5 / len(tail.split()),
                        probability=0.99,
                    )
                    for i, w in enumerate(tail.split())
                ],
            ),
        ],
        {},
    )


def test_local_restart_requires_explicit_abandonment_and_literal_coverage():
    tr = _restart(
        "Vamos falar sobre o modelo de", "Vamos falar sobre o modelo de negócio hoje"
    )
    candidates = find_candidates(tr)
    decisions = local_decisions(tr, candidates)
    assert (
        decisions
        and validate_editorial(dict(decisions=decisions), candidates, tr)[0][
            "replacement_proven"
        ]
    )
    deliberate = _restart(
        "O assunto merece bastante atenção",
        "O assunto merece bastante atenção hoje",
        ".",
    )
    assert not local_decisions(deliberate, find_candidates(deliberate))
    expressive = _restart(
        "O assunto merece bastante atenção", "O assunto merece bastante atenção hoje"
    )
    assert not local_decisions(expressive, find_candidates(expressive))
    number = _restart("O valor foi 100 reais", "O valor foi 200 reais")
    candidates = find_candidates(number)
    decision = local_decisions(number, candidates)
    assert (
        not decision
        or not validate_editorial(dict(decisions=decision), candidates, number)[0][
            "replacement_proven"
        ]
    )
    negation = _restart("Eu não quero repetir isso", "Eu quero repetir isso agora")
    candidates = find_candidates(negation)
    decision = local_decisions(negation, candidates)
    assert (
        not decision
        or not validate_editorial(dict(decisions=decision), candidates, negation)[0][
            "replacement_proven"
        ]
    )


def test_gate_counts_only_applied_correct_cuts_per_class(jobs, ready):
    last = None
    for i in range(5):
        job = ready(name=f"calibration-{i}.mp4")
        jid = job["job_id"]
        edit = jobs.read(jid, "edit.json")
        cuts = (
            [
                dict(
                    start=4 + j * 2,
                    end=5 + j * 2,
                    origin="automatic",
                    suggestion_id=f"p{j}",
                )
                for j in range(2)
            ]
            if i < 3
            else []
        )
        edit["cuts"] = cuts
        jobs.write(jid, "edit.json", edit)
        applied = [
            dict(
                id=c["suggestion_id"],
                kind="adaptive_silence",
                start=c["start"],
                end=c["end"],
                intact=True,
            )
            for c in cuts
        ]
        jobs.write(
            jid,
            "automatic_report.json",
            dict(
                revision=0,
                suggestions=[],
                applied_cuts=applied,
                policy_hash=acceptance.policy_hash(),
            ),
        )
        worker = Worker()
        exports = Exports(
            jobs, worker, lambda snapshot, path, root: (path.write_bytes(b"mp4"), {})[1]
        )
        exports.request(jid, 0)
        worker.queue.join()
        worker.close()
        acceptance.record(
            jobs,
            jid,
            dict(
                expected_revision=0,
                labels={c["id"]: "correct" for c in applied},
                watched_complete=True,
                approved=True,
                severe_errors=0,
                naturalness=4,
                missed_errors=0,
                correction_minutes=0,
            ),
        )
        gate = acceptance.gate(jobs)
        assert gate["classes"]["adaptive_silence"]["enabled"] == (i == 4)
        assert not gate["classes"]["retake"]["enabled"]
        last = jid
    mutate(jobs, last, dict(expected_revision=0, action="marker", source_time=1))
    assert not acceptance.gate(jobs)["enabled"]


def test_evaluation_no_candidates_reports_no_safe_cuts(jobs, ready):
    job = ready()
    jobs.update(job["job_id"], editing_mode="evaluation")
    worker = Worker()
    service = Automatic(jobs, worker, None, Exports(jobs, worker), jobs.root / "none")
    service.request(job["job_id"], dict(mode="evaluation", export=False), 0)
    worker.queue.join()
    worker.close()
    report = service.status(job["job_id"])["report"]
    assert report["message"] == "Nenhum corte seguro encontrado"
    assert not report["applied_cuts"] and not acceptance.gate(jobs)["enabled"]


def test_context_label_reports_unavailable_provider(jobs, ready):
    job = ready()
    jobs.update(job["job_id"], editing_mode="evaluation",
                name=job["name"] + " · Edição experimental · Contexto")
    worker = Worker()
    service = Automatic(jobs, worker, None, Exports(jobs, worker), jobs.root / "none")
    service.request(job["job_id"], dict(mode="evaluation", export=False, send_text=True), 0)
    worker.queue.join()
    worker.close()
    assert service.status(job["job_id"])["report"]["provider"] != "complete"
    assert jobs.info(job["job_id"])["name"].endswith(" · Contexto indisponível")


def test_local_restart_cannot_use_replacement_removed_by_baseline(
    jobs, ready, monkeypatch
):
    tr = _restart(
        "Vamos falar sobre o modelo de", "Vamos falar sobre o modelo de negócio hoje"
    )
    job = ready(segments=tr["segments"])
    jid = job["job_id"]
    jobs.update(jid, editing_mode="evaluation")
    edit = jobs.read(jid, "edit.json")
    edit["cuts"] = [dict(start=3, end=4, origin="human")]
    jobs.write(jid, "edit.json", edit)
    jobs.write(jid, "suggestions.json", find_candidates(tr))
    monkeypatch.setattr(
        "video_editor.automatic.visual_context",
        lambda *args: dict(state="measured", abrupt=False),
    )
    worker = Worker()
    service = Automatic(jobs, worker, None, Exports(jobs, worker), jobs.root / "none")
    service.request(jid, dict(mode="evaluation", export=False), 0)
    worker.queue.join()
    worker.close()
    assert jobs.read(jid, "edit.json")["cuts"] == edit["cuts"]
    assert not service.status(jid)["report"]["applied_cuts"]


def test_local_intra_repeat_cut_in_evaluation_mode():
    tr = normalize(
        [
            dict(
                id="s1",
                start=1,
                end=5,
                text="Ou seja até ou seja até a palavra em inglês.",
                words=[
                    dict(word="Ou", start=1.0, end=1.2, probability=0.99),
                    dict(word="seja", start=1.2, end=1.5, probability=0.99),
                    dict(word="até", start=1.5, end=1.8, probability=0.99),
                    dict(word="ou", start=2.2, end=2.4, probability=0.99),
                    dict(word="seja", start=2.4, end=2.7, probability=0.99),
                    dict(word="até", start=2.7, end=3.0, probability=0.99),
                    dict(word="a", start=3.0, end=3.2, probability=0.99),
                    dict(word="palavra", start=3.2, end=3.7, probability=0.99),
                    dict(word="em", start=3.7, end=3.9, probability=0.99),
                    dict(word="inglês.", start=3.9, end=4.5, probability=0.99),
                ],
            )
        ],
        {},
    )
    candidates = find_candidates(tr)
    decisions = local_decisions(tr, candidates)
    assert any(d["action"] == "remove" for d in decisions)
    clean = validate_editorial(dict(decisions=decisions), candidates, tr)
    assert clean[0]["replacement_proven"] is True

