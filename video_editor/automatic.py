"""Automatic cleaning is an auditable edit, never a second timeline authority."""

import copy
import json
import subprocess
import time
import re
from pathlib import Path
import wave
import math
import numpy as np

from video_editor import (
    storage,
    media,
    activity,
    acceptance,
    contextual,
    pacing,
    takes,
    visual_presence,
    voice_presence,
)
from video_editor.cuts import tokens, find_candidates
from video_editor.cut_policy import apply_policy
from video_editor.editing import load_edit, require_revision, RevisionConflict
from video_editor.timeline import build_timeline, digest, source_to_output
from video_editor.editorial import llm_client
from video_editor.noise_profile import analyze as analyze_noise_profile
from video_editor.automatic_evaluation import local_decisions

POLICY_VERSION = 3
CONTEXT_SUFFIX = " · Contexto"
CONTEXT_UNAVAILABLE_SUFFIX = " · Contexto indisponível"
NEW_CLASSES = (
    "retake",
    "false_start",
    "correction",
    "adaptive_silence",
    "contextual_restart",
)
ACTIVE_STATES = (
    "queued",
    "preparing",
    "analyzing",
    "analyzing_takes",
    "validating",
    "checking_sequence",
    "exporting",
)


def overlap(a, b):
    return a["start"] < b["end"] and a["end"] > b["start"]


def safe_bounds(candidate, transcript, duration, fps, protected=()):
    """Check *final* frame-aligned boundaries, including words without timestamps."""
    start, end = build_timeline(duration, [candidate], fps)["cuts"][0]
    if candidate.get("removed_word_ids"):
        target = set(candidate["removed_word_ids"])
    else:
        evidence = candidate.get("evidence", [])
        evidence = set(evidence) if isinstance(evidence, list) else set()
        # Evidence includes the replacement take too. Only the original requested
        # removal may be touched; frame expansion must protect retained evidence.
        target = {
            item["id"]
            for segment in transcript["segments"]
            for item in [segment, *segment["words"]]
            if item["id"] in evidence
            and item["start"] >= candidate["start"]
            and item["end"] <= candidate["end"]
        }
    span = dict(start=start, end=end)
    for segment in transcript["segments"]:
        for word in segment["words"]:
            if (
                segment["id"] not in target
                and word["id"] not in target
                and overlap(span, word)
            ):
                return None
        if (
            not segment["words"]
            and segment["id"] not in target
            and overlap(span, segment)
        ):
            return None
    if any(overlap(span, p) for p in protected):
        return None
    return span


def audio_context(wav_path, start, end):
    """Small acoustic windows: preserve rising intonation and energetic boundaries."""
    with wave.open(str(wav_path), "rb") as wav:
        rate = wav.getframerate()

        def samples(left, right):
            wav.setpos(min(wav.getnframes(), max(0, int(left * rate))))
            return (
                np.frombuffer(
                    wav.readframes(max(0, int((right - left) * rate))), dtype="<i2"
                ).astype(np.float32)
                / 32768
            )

        before = samples(max(0, start - 0.4), start)
        after = samples(end, end + 0.2)

    def pitch(values):
        if len(values) < 320 or float(np.sqrt(np.mean(values * values))) < 0.003:
            return None
        values = values - np.mean(values)
        corr = np.correlate(values[::4], values[::4], mode="full")[
            len(values[::4]) - 1 :
        ]
        low, high = int(rate / 4 / 350), min(int(rate / 4 / 70), len(corr))
        if high <= low or corr[0] <= 0:
            return None
        lag = low + int(np.argmax(corr[low:high]))
        return float(rate / 4 / lag) if corr[lag] / corr[0] > 0.5 else None

    mid = len(before) // 2
    p1, p2 = pitch(before[:mid]), pitch(before[mid:])
    return dict(
        rising_intonation=bool(p1 and p2 and p2 / p1 > 1.2),
        boundary_peak=float(
            max(
                np.max(np.abs(before)) if len(before) else 0,
                np.max(np.abs(after)) if len(after) else 0,
            )
        ),
    )


def _sample_frames(source, timestamp, origin, cache=None):
    """Sample 3 low-resolution frames at a specific timestamp, using an optional cache."""
    seek_time = max(0, timestamp + origin)
    cache_key = (str(source), round(seek_time, 4))
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    result = subprocess.run(
        [
            media.binary("ffmpeg"),
            "-nostdin",
            "-v",
            "error",
            "-ss",
            str(seek_time),
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-frames:v",
            "3",
            "-vf",
            "fps=25,scale=96:54",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
        timeout=15,
    )
    if result.returncode or not result.stdout or len(result.stdout) % (96 * 54 * 3):
        entry = (None, None)
    else:
        raw = result.stdout
        median = np.median(
            np.frombuffer(raw, dtype=np.uint8)
            .reshape(-1, 96 * 54 * 3)
            .astype(float),
            axis=0,
        )
        entry = (raw, median)

    if cache is not None:
        if len(cache) > 256:
            del cache[next(iter(cache))]
        cache[cache_key] = entry
    return entry


def visual_context(source, info, start, end, cache=None):
    """Compare short low-resolution sequences on both sides of the join."""
    origin = info.get("origin", 0) if info.get("timing_contract") == 2 else 0
    t_left = max(0, start - 0.12)
    t_right = min(info["duration"] - 0.04, end + 0.01)

    raw0, med0 = _sample_frames(source, t_left, origin, cache=cache)
    if raw0 is None:
        return dict(state="unavailable")

    raw1, med1 = _sample_frames(source, t_right, origin, cache=cache)
    if raw1 is None:
        return dict(state="unavailable")

    difference = float(np.mean(np.abs(med0 - med1)) / 255)
    return dict(
        state="measured",
        difference=difference,
        abrupt=difference > 0.35,
        sampled_frames=[len(raw0) // (96 * 54 * 3), len(raw1) // (96 * 54 * 3)],
    )


def _call_visual_context(source, info, start, end, cache=None):
    """Call visual_context supporting both real implementation and 4-argument test mocks."""
    try:
        return visual_context(source, info, start, end, cache=cache)
    except TypeError:
        return visual_context(source, info, start, end)


def refine_visual(source, info, candidate, transcript, protected, cache=None):
    if cache is None:
        cache = {}
    original = _call_visual_context(source, info, candidate["start"], candidate["end"], cache=cache)
    if original.get("state") != "measured" or not original.get("abrupt"):
        return candidate, original
    best, evidence = candidate, original
    removed = {i for i in candidate.get("removed_word_ids", [])}
    target = [
        w for s in transcript["segments"] for w in s["words"] if w["id"] in removed
    ]
    frame = 1 / info["fps"]
    for left, right in ((frame, 0), (0, frame), (frame, frame), (2 * frame, 2 * frame)):
        trial = dict(
            candidate, start=candidate["start"] + left, end=candidate["end"] - right
        )
        if trial["start"] >= trial["end"] or any(
            w["start"] < trial["start"] or w["end"] > trial["end"] for w in target
        ):
            continue
        bounds = safe_bounds(
            trial, transcript, info["duration"], info["fps"], protected
        )
        if not bounds:
            continue
        trial.update(bounds)
        value = _call_visual_context(source, info, trial["start"], trial["end"], cache=cache)
        if (
            value.get("state") == "measured"
            and value["difference"] < evidence["difference"]
        ):
            best, evidence = trial, value
    evidence["boundary_refined"] = best != candidate
    return best, evidence


def classify(transcript, suggestions, duration, fps, wav=None, protected=()):
    """Preserve expressive pauses; novel removal classes remain human-gated."""
    segments = transcript["segments"]
    all_words = sorted(
        (w for s in segments for w in s["words"]), key=lambda w: (w["start"], w["end"])
    )
    output = copy.deepcopy(suggestions)
    gaps = [
        b["start"] - a["end"]
        for a, b in zip(all_words, all_words[1:])
        if 0.12 <= b["start"] - a["end"] <= 2.5
    ]
    rhythm = sorted(gaps)[len(gaps) // 2] if gaps else 0.65
    gap_events = [
        (b["start"], b["start"] - a["end"])
        for a, b in zip(all_words, all_words[1:])
        if 0.12 <= b["start"] - a["end"] <= 2.5
    ]
    for candidate in output:
        nearby_gaps = [
            gap
            for timestamp, gap in gap_events
            if candidate["start"] - 30 <= timestamp <= candidate["end"] + 30
        ]
        local_rhythm = (
            sorted(nearby_gaps)[len(nearby_gaps) // 2]
            if len(nearby_gaps) >= 3
            else rhythm
        )
        candidate["evaluation"] = dict(
            policy=POLICY_VERSION, local_pause_seconds=round(local_rhythm, 3)
        )
        paced = isinstance(candidate.get("evidence"), dict) and candidate["evidence"].get("paced")
        # Paced proposals already chose their residual pause by context (pacing.py).
        if candidate["kind"] in ("silence", "adaptive_silence") and not paced:
            left = [w for w in all_words if w["end"] <= candidate["start"]]
            right = [w for w in all_words if w["start"] >= candidate["end"]]
            previous = left[-1] if left else None
            following = right[0] if right else None
            preceding = " ".join(w["text"] for w in left[-12:])
            continuation = " ".join(w["text"] for w in right[:12])
            sentence = re.split(r"[.!?…]", preceding.rstrip(".!?…"))[-1]
            pause_dur = candidate["end"] - candidate["start"]
            expressive = (
                pause_dur <= 2.0
                and (
                    not previous
                    or not following
                    or previous["text"].rstrip().endswith(("?", "!", "...", "…"))
                    or (len(tokens(sentence)) <= 4)
                    or any(any(c.isdigit() for c in t) for t in tokens(preceding)[-6:])
                    or " ".join(tokens(continuation)).startswith(
                        ("mas ", "porem ", "so que ", "por exemplo ")
                    )
                )
            )
            features = (
                audio_context(wav, previous["end"], following["start"])
                if wav and previous and following
                else {}
            )
            candidate["evaluation"].update(
                expressive_pause=expressive, acoustic_context=features
            )
            if expressive or (pause_dur <= 2.0 and features.get("rising_intonation")):
                candidate.update(
                    auto=False,
                    reason="Pausa possivelmente expressiva; preservada para manter seu ritmo.",
                )
            elif candidate.get("auto") or candidate["kind"] == "adaptive_silence":
                # Retain at least the observed local rhythm, rather than one fixed pause everywhere.
                target = min(1.2, max(0.65, local_rhythm))
                extra = max(0, target - 0.65) / 2
                if candidate["end"] - candidate["start"] - 2 * extra < 0.08:
                    candidate.update(
                        auto=False,
                        reason="Esta pausa já acompanha o ritmo da fala; preservada.",
                    )
                    candidate["evaluation"]["rhythm_keep"] = True
                else:
                    candidate.update(
                        start=candidate["start"] + extra, end=candidate["end"] - extra
                    )
        if candidate["kind"] in NEW_CLASSES:
            candidate["auto"] = False
            candidate["evaluation"]["human_gate"] = "pending_five_videos"
        if candidate.get("auto"):
            bounds = safe_bounds(candidate, transcript, duration, fps, protected)
            if bounds:
                candidate.update(bounds)
            else:
                candidate.update(
                    auto=False,
                    reason="Limite de frame toca fala ou trecho protegido; preservado.",
                )
    return output


def editorial_packet(transcript, candidates, summary):
    ids = set(
        i
        for c in candidates
        for i in c.get("evidence", [])
        if isinstance(c.get("evidence"), list)
    )
    segments = transcript["segments"]
    indices = {
        i
        for i, s in enumerate(segments)
        if s["id"] in ids or any(w["id"] in ids for w in s["words"])
    }
    nearby = {
        j for i in indices for j in range(max(0, i - 2), min(len(segments), i + 3))
    }
    from video_editor.settings import PERSONA_PATH

    persona_path = PERSONA_PATH
    persona = (
        persona_path.read_text(encoding="utf-8")[:20000]
        if persona_path.exists()
        else ""
    )
    payload = dict(
        global_context=summary,
        author_style=persona,
        candidates=candidates,
        context=[
            dict(id=s["id"], text=s["text"], words=s["words"])
            for i, s in enumerate(segments)
            if i in nearby
        ],
    )
    return (
        "Avalie substituição de tomadas em português. Preserve a persona autoral, humor seco, pausas expressivas, ênfase, exemplos, ressalvas, números, "
        "negações e informação exclusiva. Não sugira reorganização. Julgue também se a emenda mantém "
        'o sentido antes/depois. Retorne SOMENTE JSON {"decisions":[{"suggestion_id":"id",'
        '"action":"keep|remove|uncertain","replacement_segment_ids":["id"],'
        '"replacement_word_ids":["id de palavra, para repetição dentro da frase"],'
        '"information_loss":false,"coherent_join":true,"reason":"motivo"}]}. '
        "Os IDs devem existir no pacote.\n" + json.dumps(payload, ensure_ascii=False)
    )


def validate_editorial(result, candidates, transcript):
    if (
        not isinstance(result, dict)
        or set(result) != {"decisions"}
        or not isinstance(result["decisions"], list)
    ):
        raise ValueError("Resposta editorial inválida.")
    known = {c["id"]: c for c in candidates}
    segments = {s["id"]: s for s in transcript["segments"]}
    words = {w["id"]: w for s in transcript["segments"] for w in s["words"]}
    clean = []
    seen = set()
    for value in result["decisions"]:
        if (
            not isinstance(value, dict)
            or value.get("suggestion_id") not in known
            or value["suggestion_id"] in seen
        ):
            raise ValueError("Referência editorial inválida.")
        replacements = value.get("replacement_segment_ids")
        replacement_words = value.get("replacement_word_ids", [])
        if (
            value.get("action") not in ("keep", "remove", "uncertain")
            or not isinstance(replacements, list)
            or not all(isinstance(i, str) and i in segments for i in replacements)
            or not isinstance(replacement_words, list)
            or not all(isinstance(i, str) and i in words for i in replacement_words)
            or type(value.get("information_loss")) is not bool
            or type(value.get("coherent_join")) is not bool
            or not isinstance(value.get("reason"), str)
            or len(value["reason"]) > 2000
        ):
            raise ValueError("Decisão editorial inválida.")
        candidate = known[value["suggestion_id"]]
        removed = [s for s in transcript["segments"] if overlap(candidate, s)]
        replacement = [segments[i] for i in replacements]
        # A model's confidence never authorizes dropping different numbers/negations/entities.
        old_tokens = tokens(" ".join(s["text"] for s in removed))
        new_tokens = tokens(" ".join(s["text"] for s in replacement))
        strict_replacement = (
            bool(replacement)
            and all(s["start"] >= candidate["end"] for s in replacement)
            and len(new_tokens) >= len(old_tokens)
            and new_tokens[: len(old_tokens)] == old_tokens
        )
        explicit_correction = False
        if candidate.get("kind") == "correction" and len(replacement) == 1:
            corrected = tokens(replacement[0]["text"])
            for marker in (["corrigindo"], ["nao", "na", "verdade"]):
                if corrected[: len(marker)] == marker:
                    corrected = corrected[len(marker) :]
                    changes = [(a, b) for a, b in zip(old_tokens, corrected) if a != b]
                    explicit_correction = (
                        len(old_tokens) == len(corrected)
                        and len(changes) == 1
                        and not any(
                            a in ("nao", "nunca", "jamais", "sem")
                            or b in ("nao", "nunca", "jamais", "sem")
                            for a, b in changes
                        )
                        and all(
                            w["probability"] >= 0.9
                            for s in replacement
                            for w in s["words"]
                        )
                        and bool(replacement[0]["words"])
                        and replacement[0]["start"] >= candidate["end"]
                    )
                    break
        strict_replacement = strict_replacement or explicit_correction
        if strict_replacement:
            strict_replacement = all(
                s["words"]
                and all(w["probability"] >= 0.9 for w in s["words"])
                and tokens(" ".join(w["text"] for w in s["words"])) == tokens(s["text"])
                for s in replacement
            )
        if candidate.get("removed_word_ids") and replacement_words:
            original = [words[i] for i in candidate["removed_word_ids"]]
            replacing = [words[i] for i in replacement_words]
            old_tokens = tokens(" ".join(w["text"] for w in original))
            new_tokens = tokens(" ".join(w["text"] for w in replacing))
            strict_replacement = (
                all(w["start"] >= candidate["end"] for w in replacing)
                and len(new_tokens) >= len(old_tokens)
                and new_tokens[: len(old_tokens)] == old_tokens
                and all(
                    a["end"] <= b["start"] + 0.02
                    for a, b in zip(replacing, replacing[1:])
                )
                and all(w["probability"] >= 0.9 for w in replacing)
            )
        clean.append(
            dict(
                suggestion_id=value["suggestion_id"],
                action=value["action"],
                reason=value["reason"],
                replacement_segment_ids=replacements,
                replacement_word_ids=replacement_words,
                replacement_proven=strict_replacement
                and not value["information_loss"]
                and value["coherent_join"],
                explicit_correction=explicit_correction,
            )
        )
        seen.add(value["suggestion_id"])
    return clean


def background_voice_candidates(jobs, job_id, job, transcript, edit=None):
    if not voice_presence.available():
        return [], dict(state="unavailable", reason="dependencies_missing")
    presence = None
    presence_path = jobs.path(job_id, "visual/presence.json")
    if presence_path.exists():
        try:
            presence = jobs.read(job_id, "visual/presence.json")
        except (OSError, ValueError):
            presence = None
    try:
        value = voice_presence.analyze(
            jobs.path(job_id, "audio/extracted_16k.wav"),
            transcript,
            job["media"]["duration"],
            job["source"],
            presence=presence,
            cache_path=jobs.path(job_id, "voice/voice_presence.json"),
        )
    except Exception as error:
        return [], dict(state="failed", reason=str(error)[:300])
    state = dict(
        state=value.get("state"),
        reason=value.get("reason"),
        intervals=len(value.get("intervals") or []),
        windows=value.get("windows"),
        reference_windows=value.get("reference_windows"),
        threshold=value.get("threshold"),
    )
    if value.get("state") != "ok":
        return [], state
    words = sorted(
        (word for segment in transcript["segments"] for word in segment["words"]),
        key=lambda word: (word["start"], word["end"]),
    )
    keeps = None
    if edit is not None:
        timeline = build_timeline(
            job["media"]["duration"], edit["cuts"], job["media"].get("fps")
        )
        keeps = [(k["source_start"], k["source_end"]) for k in timeline["keeps"]]
    spans = []
    for interval in value.get("intervals", []):
        if keeps is None:
            spans.append((interval["start"], interval["end"], interval))
            continue
        # Only what is still in the cut deserves a review card.
        for left, right in keeps:
            start = max(interval["start"], left)
            end = min(interval["end"], right)
            if end - start >= 1.0:
                spans.append((start, end, interval))
    result = []
    for start, end, interval in spans:
        inside = [word for word in words if word["end"] > start and word["start"] < end]
        if not inside:
            continue
        index = len(result)
        evidence = interval.get("evidence", {})
        score = float(interval.get("score", 0.0))
        result.append(
            dict(
                id=f"voice-{index + 1}",
                kind="background_voice",
                start=round(start, 3),
                end=round(end, 3),
                auto=False,
                removed_word_ids=[word["id"] for word in inside],
                evidence=dict(
                    score=round(score, 3),
                    rms_ratio=evidence.get("rms_ratio"),
                    windows=evidence.get("windows"),
                    speech=[word["text"] for word in inside][:40],
                ),
                reason=(
                    f"Voz provavelmente de outra pessoa "
                    f"(similaridade {score:.2f}); revise."
                ),
            )
        )
    state["suggestions"] = len(result)
    return result, state


class Automatic:
    def __init__(self, jobs, worker, pipeline, exports, config_path):
        self.jobs, self.worker, self.pipeline, self.exports = (
            jobs,
            worker,
            pipeline,
            exports,
        )
        self.config_path = Path(config_path)

    @property
    def policy_hash(self):
        # Live, not cached at startup: Studio edits can move the channel profile.
        return acceptance.policy_hash()

    def recover(self, job_id):
        """Only requests with current identities may resume without a user action."""
        state = self.status(job_id)
        if state["state"] not in ACTIVE_STATES + ("interrupted",) or not state.get(
            "input_signature"
        ):
            return False
        info = self.jobs.info(job_id)
        options = state["options"]
        signature = digest(
            dict(
                source=info["source"],
                config=info["config"],
                options=options,
                policy=self.policy_hash,
            )
        )
        if signature != state["input_signature"]:
            return False
        self.jobs.validate(job_id)
        revision = None
        if info["state"] == "ready":
            from video_editor.contextual import finish_refinement

            intent = finish_refinement(self.jobs, job_id)
            _, edit, tr = load_edit(self.jobs, job_id)
            revision = edit["revision"]
            checkpoint_path = self.jobs.path(job_id, "automatic/checkpoint.json")
            checkpoint = (
                self.jobs.read(job_id, "automatic/checkpoint.json")
                if checkpoint_path.exists()
                else {}
            )
            if (
                checkpoint.get("input_signature") == signature
                and checkpoint.get("policy_hash") == self.policy_hash
                and checkpoint.get("edit_hash") == digest(edit)
                and checkpoint.get("transcript_hash") == digest(tr)
                and checkpoint.get("report_hash")
                == digest(self.jobs.read(job_id, "automatic_report.json"))
            ):
                state["applied_revision"] = revision
            elif state.get("input_edit_hash") == digest(edit) or (
                intent
                and intent["before_edit_hash"] == state.get("input_edit_hash")
                and intent["after_edit_hash"] == digest(edit)
            ):
                state.pop("applied_revision", None)
            else:
                return False
        state["state"] = "interrupted"
        self.jobs.write(job_id, "automatic.json", state)
        self.request(job_id, options, revision)
        return True

    def create_evaluation(self, job_id, revision, send_text=False):
        from video_editor.automatic_evaluation import clone_prepared

        previous = self.status(job_id)
        if previous["state"] in ACTIVE_STATES:
            raise RevisionConflict("Aguarde a conclusão do processamento anterior.")
        child = clone_prepared(self.jobs, job_id, revision)
        if send_text:
            child = self.jobs.update(
                child["job_id"], name=child["name"] + CONTEXT_SUFFIX
            )
        self.request(
            child["job_id"],
            dict(send_text=send_text, export=True, mode="evaluation"),
            0,
        )
        return self.jobs.info(child["job_id"])

    def status(self, job_id):
        path = self.jobs.path(job_id, "automatic.json")
        result = (
            self.jobs.read(job_id, "automatic.json")
            if path.exists()
            else dict(state="not_requested")
        )
        report_path = self.jobs.path(job_id, "automatic_report.json")
        if report_path.exists():
            report = self.jobs.read(job_id, "automatic_report.json")
            result["report"] = report
            result["stale"] = (
                report.get("revision")
                != self.jobs.read(job_id, "edit.json")["revision"]
            )
            result["engine_stale"] = (
                report.get("policy_hash") != acceptance.policy_hash()
            )
            result["stale"] = result["stale"] or result["engine_stale"]
        return result

    def request(self, job_id, options, expected_revision=None):
        from video_editor.models import AutomaticOptions

        options = AutomaticOptions(**options).model_dump()
        with self.jobs.lock(job_id):
            self.jobs.validate(job_id)
            previous = self.status(job_id)
            if previous["state"] in ACTIVE_STATES:
                raise RevisionConflict("Edição automática já está em andamento.")
            info = self.jobs.info(job_id)
            if info.get("editing_mode", "standard") != options["mode"]:
                if info.get("evaluation_parent") or info["state"] == "ready":
                    raise ValueError(
                        "Crie um job separado para alterar o modo de edição."
                    )
                info["editing_mode"] = options["mode"]
                self.jobs.write(job_id, "job.json", info)
            if info["state"] in ("queued", "processing"):
                raise RevisionConflict("Preparação em andamento.")
            if info["state"] == "ready":
                require_revision(self.jobs.read(job_id, "edit.json"), expected_revision)
            resume = (
                previous.get("applied_revision")
                if previous["state"] in ("failed", "interrupted")
                else None
            )
            if previous.get("engine_stale"):
                resume = None
            if (
                info["state"] == "ready"
                and resume != self.jobs.read(job_id, "edit.json")["revision"]
            ):
                resume = None
            value = dict(
                state="queued",
                options=dict(options),
                created_at=storage.now(),
                resume_revision=resume,
                input_signature=digest(
                    dict(
                        source=info["source"],
                        config=info["config"],
                        options=options,
                        policy=self.policy_hash,
                    )
                ),
                input_edit_hash=digest(self.jobs.read(job_id, "edit.json"))
                if info["state"] == "ready"
                else None,
            )
            self.jobs.write(job_id, "automatic.json", value)
            if info["state"] != "ready":
                info.update(state="queued", stage="automatic_queued", error=None)
                self.jobs.write(job_id, "job.json", info)
        try:
            self.worker.submit(self.run, job_id)
        except Exception:
            self.jobs.write(
                job_id,
                "automatic.json",
                dict(
                    value, state="failed", error="Fila indisponível. Retome a edição."
                ),
            )
            if info["state"] != "ready":
                self.jobs.update(job_id, state="failed", error="Fila indisponível.")
            raise
        return value

    def _prepare_run(self, job_id, state, stage):
        jobs = self.jobs
        stage("preparing")
        if jobs.info(job_id)["state"] != "ready":
            self.pipeline.prepare(job_id)

        contextual.finish_refinement(jobs, job_id)
        job, edit, transcript = load_edit(jobs, job_id)
        refinement = dict(state="not_requested")
        refinement_wav = jobs.path(job_id, "audio/extracted_16k.wav")
        if (
            state["options"]["send_text"]
            and refinement_wav.exists()
            and state.get("resume_revision") is None
        ):
            stage("analyzing_takes")
            refinement = contextual.refine_job(
                jobs, job_id, transcript, edit, refinement_wav
            )
            job, edit, transcript = load_edit(jobs, job_id)
            state["input_edit_hash"] = digest(edit)
        if state.get("input_edit_hash") is None:
            state["input_edit_hash"] = digest(edit)
        revision = edit["revision"]
        resume = state.get("resume_revision")
        if resume is not None and resume != revision:
            raise RevisionConflict(
                "Edição humana mudou após a interrupção; preserve os ajustes e solicite novamente."
            )
        return job, edit, transcript, revision, refinement

    def _detect_candidates(self, job_id, job, edit, transcript):
        jobs = self.jobs
        wav = jobs.path(job_id, "audio/extracted_16k.wav")
        found = jobs.read(job_id, "suggestions.json")
        voice_state = dict(state="not_available")
        if wav.exists() and job.get("render_audio"):
            found = [s for s in found if s["kind"] == "legacy"] + apply_policy(
                transcript,
                find_candidates(transcript),
                job["media"]["duration"],
                job["media"]["fps"],
            )
            path = jobs.path(job_id, "audio/activity.json")
            if path.exists():
                found += activity.silence_candidates(
                    transcript,
                    jobs.read(job_id, "audio/activity.json"),
                    job["media"]["duration"],
                )
            found += activity.adaptive_for_job(
                jobs, job_id, transcript, job["media"]["duration"]
            )
            from video_editor.channel_profile import detect_speech_gaps
            found += detect_speech_gaps(transcript, job["media"]["duration"], wav=wav)
            found = takes.merge(found, takes.candidates(transcript))
            found += visual_presence.candidates(jobs, job_id, job, edit, transcript)
            voice_candidates, voice_state = background_voice_candidates(
                jobs, job_id, job, transcript, edit
            )
            found += voice_candidates
            found.sort(key=lambda s: (s["start"], s["end"]))
        profile = dict(state="not_available")
        if wav.exists() and job.get("render_audio"):
            proposals, profile = analyze_noise_profile(
                transcript, wav, found, job["render_audio"]["path"]
            )
            found = [
                s for s in found if s["kind"] != "adaptive_silence"
            ] + proposals
            jobs.write(job_id, "audio/background_profile.json", profile)
        suggestions = classify(
            transcript,
            found,
            job["media"]["duration"],
            job["media"]["fps"],
            wav if wav.exists() else None,
            edit["protected"],
        )
        return found, suggestions, profile, voice_state

    def _resolve_contextual_and_editorial(
        self, job_id, job, transcript, edit, found, suggestions, state, stage
    ):
        jobs = self.jobs
        wav = jobs.path(job_id, "audio/extracted_16k.wav")
        notes = []
        provider = "disabled"
        context_report = dict(state="disabled", protected=[])
        contextual_suggestions = []
        editorial_protected = list(edit["protected"])
        if state["options"]["send_text"]:
            contextual_suggestions, editorial_protected, context_report = (
                contextual.plan(
                    jobs,
                    job_id,
                    transcript,
                    self.config_path,
                    edit["protected"],
                    stage,
                )
            )
            suggestions = classify(
                transcript,
                found,
                job["media"]["duration"],
                job["media"]["fps"],
                wav if wav.exists() else None,
                editorial_protected,
            )
            for item in contextual_suggestions:
                item["evaluation"] = dict(
                    policy=POLICY_VERSION, editorial=item.pop("editorial")
                )
                suggestions.append(item)
        # Cached summaries from explicit briefings are context only, never cut authority.
        brief = jobs.path(job_id, "editorial/brief.json")
        previous = (
            jobs.read(job_id, "editorial/brief.json")
            if brief.exists()
            else None
        )
        if previous and previous["transcript_hash"] == digest(transcript):
            summary = previous["summary"]
        else:
            # Every sentence is represented; never pretend a sampled outline covers the whole argument.
            summary = "\n".join(
                f"{s['id']} ({s['start']:.2f}–{s['end']:.2f}): {s['text']}"
                for s in transcript["segments"]
            )
            jobs.write(
                job_id,
                "editorial/automatic_context.json",
                dict(transcript_hash=digest(transcript), text=summary),
            )
        ambiguous = [
            s
            for s in suggestions
            if s["kind"] in ("retake", "false_start", "correction")
        ]
        if (
            state["options"]["send_text"]
            and ambiguous
            and len(summary) <= 120000
            and context_report["state"] != "complete"
        ):
            for left in range(0, len(ambiguous), 20):
                batch = ambiguous[left : left + 20]
                packet = editorial_packet(transcript, batch, summary)
                cache = (
                    "editorial/automatic/"
                    + digest(
                        dict(
                            prompt=packet,
                            policy=POLICY_VERSION,
                            provider=llm_client.cache_signature(
                                self.config_path
                            ),
                        )
                    )
                    + ".json"
                )
                try:
                    path = jobs.path(job_id, cache)
                    result = (
                        jobs.read(job_id, cache)
                        if path.exists()
                        else llm_client.generate(self.config_path, packet)
                    )
                    decisions = validate_editorial(result, batch, transcript)
                    jobs.write(job_id, cache, result)
                    notes.extend(decisions)
                    provider = "complete"
                except Exception:
                    provider = "unavailable_or_invalid"
                    break
        elif state["options"]["send_text"] and len(summary) > 120000:
            provider = "context_too_large_preserved"
        notes_by_id = {n["suggestion_id"]: n for n in notes}

        local = local_decisions(transcript, suggestions)
        if local:
            local_notes = validate_editorial(
                dict(decisions=local), suggestions, transcript
            )
            for note in local_notes:
                note["origin"] = "local"
                notes_by_id.setdefault(note["suggestion_id"], note)
            notes = list(notes_by_id.values())
        return suggestions, editorial_protected, context_report, notes_by_id, provider

    def _validate_and_refine(
        self,
        job_id,
        job,
        transcript,
        edit,
        suggestions,
        notes_by_id,
        editorial_protected,
        context_report,
        evaluation_mode,
        state,
        stage,
    ):
        jobs = self.jobs
        human_gate = acceptance.gate(jobs)
        stage("validating")

        def allowed(kind):
            if evaluation_mode:
                return True
            from video_editor.channel_profile import load_profile
            prof = load_profile()
            # Generic retake/false_start candidates stay behind the five-video human gate;
            # only the strict take classes the creator approved bypass it.
            if kind in takes.AUTO_KINDS:
                return prof.get("auto_cut_takes", True)
            if kind == "repeated_idea":
                return False
            if kind == "adaptive_silence" and prof.get("auto_cut_silence", True):
                return True
            if kind == "contextual_restart":
                return human_gate.get("classes", {}).get(
                    kind, {}
                ).get("enabled", False)
            return human_gate.get("classes", {}).get(
                kind, {}
            ).get("enabled", human_gate["enabled"])

        visual_count = 0
        visual_frame_cache = {}
        for s in suggestions:
            if s["id"] in notes_by_id:
                s["evaluation"]["editorial"] = notes_by_id[s["id"]]
            if s["kind"] in takes.KINDS and s.get("auto") and not allowed(s["kind"]):
                s.update(auto=False, reason=s["reason"] + " Corte automático de tomadas desativado no perfil.")
            if s["kind"] in NEW_CLASSES:
                decision = notes_by_id.get(s["id"], {})
                if s["kind"] == "contextual_restart":
                    decision = s["evaluation"]["editorial"]
                    notes_by_id[s["id"]] = decision
                s["evaluation"]["human_gate"] = (
                    "evaluation_only"
                    if evaluation_mode
                    else human_gate["state"]
                )
                if s["kind"] == "adaptive_silence" and allowed(s["kind"]):
                    proof = s["evidence"]
                    evaluation = s["evaluation"]
                    if (
                        proof.get("native_clear")
                        and proof.get("vad_clear")
                        and proof.get("background_match")
                        and not evaluation.get("expressive_pause")
                        and not evaluation.get("rhythm_keep")
                        and not (
                            (s["end"] - s["start"] <= 2.0)
                            and evaluation.get("acoustic_context", {}).get(
                                "rising_intonation"
                            )
                        )
                    ):
                        bounds = safe_bounds(
                            s,
                            transcript,
                            job["media"]["duration"],
                            job["media"]["fps"],
                            edit["protected"],
                        )
                        if bounds:
                            s.update(bounds, auto=True)
                if s["kind"] == "contextual_restart" and allowed(s["kind"]):
                    removed_ids = set(s["removed_word_ids"])
                    removed = [
                        w
                        for seg in transcript["segments"]
                        for w in seg["words"]
                        if w["id"] in removed_ids
                    ]
                    proposed = dict(
                        s,
                        start=max(0, removed[0]["start"] - 0.06),
                        end=min(
                            job["media"]["duration"], removed[-1]["end"] + 0.06
                        ),
                    )
                    bounds = safe_bounds(
                        proposed,
                        transcript,
                        job["media"]["duration"],
                        job["media"]["fps"],
                        editorial_protected,
                    )
                    if bounds:
                        s.update(bounds, auto=True)
                    else:
                        s["reason"] = (
                            "Limite de frame toca fala ou proteção editorial; preservado."
                        )
                if s["kind"] != "contextual_restart" and (
                    allowed(s["kind"])
                    and decision.get("action") == "remove"
                    and decision.get("replacement_proven")
                ):
                    target = [
                        x
                        for x in transcript["segments"]
                        if x["start"] == s["start"] and x["end"] == s["end"]
                    ]
                    if (
                        len(target) == 1
                        and target[0]["words"]
                        and all(
                            w["probability"] >= 0.9 for w in target[0]["words"]
                        )
                        and tokens(
                            " ".join(w["text"] for w in target[0]["words"])
                        )
                        == tokens(target[0]["text"])
                    ):
                        proposed = dict(
                            s,
                            evidence=[target[0]["id"]],
                            start=max(0, target[0]["words"][0]["start"] - 0.06),
                            end=min(
                                job["media"]["duration"],
                                target[0]["words"][-1]["end"] + 0.06,
                            ),
                        )
                        bounds = safe_bounds(
                            proposed,
                            transcript,
                            job["media"]["duration"],
                            job["media"]["fps"],
                            editorial_protected,
                        )
                        if bounds:
                            s.update(bounds, auto=True)
                    elif s.get("removed_word_ids"):
                        words = {
                            w["id"]: w
                            for x in transcript["segments"]
                            for w in x["words"]
                        }
                        removed = [words[i] for i in s["removed_word_ids"] if i in words]
                        if removed:
                            all_words = sorted([w for x in transcript["segments"] for w in x["words"]], key=lambda w: w["start"])
                            first_idx = next((i for i, w in enumerate(all_words) if w["id"] == removed[0]["id"]), None)
                            last_idx = next((i for i, w in enumerate(all_words) if w["id"] == removed[-1]["id"]), None)
                            prev_end = all_words[first_idx - 1]["end"] if first_idx is not None and first_idx > 0 else 0.0
                            next_start = all_words[last_idx + 1]["start"] if last_idx is not None and last_idx + 1 < len(all_words) else job["media"]["duration"]
                            fps = job["media"]["fps"]
                            st = math.ceil(max(prev_end, removed[0]["start"] - 0.04) * fps) / fps
                            en = math.floor(min(next_start, removed[-1]["end"] + 0.04) * fps) / fps
                            proposed = dict(
                                s,
                                evidence=s["removed_word_ids"],
                                removed_word_ids=s["removed_word_ids"],
                                start=st,
                                end=en,
                            )
                            bounds = safe_bounds(
                                proposed,
                                transcript,
                                job["media"]["duration"],
                                job["media"]["fps"],
                                editorial_protected,
                            )
                            if bounds:
                                s.update(bounds, auto=True)
            if s.get("auto"):
                try:
                    refined, visual = refine_visual(
                        jobs.validate(job_id),
                        job["media"],
                        s,
                        transcript,
                        editorial_protected,
                        cache=visual_frame_cache,
                    )
                    s.update(start=refined["start"], end=refined["end"])
                    visual_count += 1
                except (RuntimeError, subprocess.TimeoutExpired):
                    visual = dict(state="unavailable")
                s["evaluation"]["visual"] = visual
                if visual.get("abrupt") or (
                    s["kind"] in NEW_CLASSES and visual["state"] != "measured"
                ):
                    s.update(
                        auto=False,
                        reason="Emenda visual abrupta; trecho preservado para revisão.",
                    )
        # One owner per pause, no choppy pair of small cuts around a short phrase and no
        # pure-silence sliver left between a pause cut and a take cut.
        pacing.seal(pacing.thin(pacing.reconcile(suggestions)), transcript, editorial_protected)
        takes.guard(suggestions, transcript)
        # Simulate the final joins: a replacement must still be present after all proposals.
        for s in suggestions:
            if s.get("auto") and s["kind"] in (
                "retake",
                "false_start",
                "correction",
                "contextual_restart",
            ):
                replacements = notes_by_id[s["id"]]["replacement_segment_ids"]
                present = [
                    x for x in transcript["segments"] if x["id"] in replacements
                ]
                word_ids = notes_by_id[s["id"]].get("replacement_word_ids", [])
                present.extend(
                    w
                    for x in transcript["segments"]
                    for w in x["words"]
                    if w["id"] in word_ids
                )
                if any(
                    overlap(cut, replacement)
                    for cut in edit["cuts"]
                    for replacement in present
                ):
                    s.update(
                        auto=False,
                        reason="A edição anterior já remove parte da tomada substituta; conteúdo preservado.",
                    )
                    continue
                if any(
                    overlap(other, r)
                    for r in present
                    for other in suggestions
                    if other["id"] != s["id"] and other.get("auto")
                ):
                    s.update(
                        auto=False,
                        reason="A tomada substituta também seria removida; conteúdo preservado.",
                    )
        # No proposal may remove an editorially protected span, including
        # local/mechanical cuts generated before the semantic map existed.
        for s in suggestions:
            if s.get("auto") and any(
                overlap(s, p) for p in editorial_protected
            ):
                s.update(
                    auto=False,
                    reason="Proteção editorial ou humana; trecho preservado.",
                )
        human_decisions = any(
            h["action"]
            in ("cut", "restore", "protect", "undo", "legacy_import")
            for h in edit["history"]
        )
        if human_decisions:
            for s in suggestions:
                if s.get("auto"):
                    s.update(
                        auto=False,
                        reason="Decisões humanas preservadas; nenhuma reaplicação automática.",
                    )
        fixed_baseline = (
            edit["cuts"]
            if human_decisions
            else [
                c
                for c in edit["cuts"]
                if c["origin"] not in ("automatic", "mechanical")
            ]
        )
        sequence_audit = dict(state="not_requested", restored=[])
        if context_report["state"] == "complete":
            sequence_audit = contextual.audit(
                jobs,
                job_id,
                transcript,
                suggestions,
                fixed_baseline,
                self.config_path,
                context_report,
                stage,
            )
            if (
                sequence_audit["state"] == "complete"
                and sequence_audit["restored"]
            ):
                first_restored = sequence_audit["restored"]
                sequence_audit = contextual.audit(
                    jobs,
                    job_id,
                    transcript,
                    suggestions,
                    fixed_baseline,
                    self.config_path,
                    context_report,
                    stage,
                )
                sequence_audit["previously_restored"] = first_restored
                if (
                    sequence_audit["state"] == "complete"
                    and sequence_audit["restored"]
                ):
                    for s in suggestions:
                        if s["kind"] == "contextual_restart":
                            s.update(
                                auto=False,
                                reason="Conferência não estabilizou; reinício contextual preservado.",
                            )
                    sequence_audit["state"] = "restored_to_local"
        return suggestions, human_gate, sequence_audit, allowed

    def _apply_cuts_and_report(
        self,
        job_id,
        job,
        transcript,
        revision,
        suggestions,
        notes_by_id,
        human_gate,
        allowed,
        provider,
        profile,
        voice_state,
        context_report,
        sequence_audit,
        refinement,
        state,
        evaluation_mode,
    ):
        jobs = self.jobs
        with jobs.lock(job_id):
            jobs.validate(job_id)
            _, latest, _ = load_edit(jobs, job_id)
            require_revision(latest, revision)
            # Replace only unreviewed initial mechanical cuts. Human decisions always win.
            mechanical_ids = {
                c.get("suggestion_id")
                for c in latest["cuts"]
                if c["origin"] in ("mechanical", "automatic")
            }
            before = {
                k: copy.deepcopy(latest[k])
                for k in ("cuts", "markers", "protected")
            }
            human_edits = any(
                h["action"]
                in ("cut", "restore", "protect", "undo", "legacy_import")
                for h in latest["history"]
            )
            if not human_edits:
                latest["cuts"] = [
                    c
                    for c in latest["cuts"]
                    if c["origin"] not in ("mechanical", "automatic")
                ]
            # Takes first: a pause inside a removed take belongs to the take's cut.
            for s in sorted(suggestions, key=lambda s: s["kind"] not in takes.AUTO_KINDS):
                if (
                    s.get("auto")
                    and (
                        s["id"] in mechanical_ids
                        or s["id"].startswith("gap-")
                        or (s["kind"] in takes.AUTO_KINDS and allowed(s["kind"]))
                        or (
                            s["kind"] in NEW_CLASSES
                            and allowed(s["kind"])
                            and not human_edits
                        )
                    )
                    and not human_edits
                    and not any(overlap(s, c) for c in latest["cuts"])
                ):
                    latest["cuts"].append(
                        dict(
                            start=s["start"],
                            end=s["end"],
                            origin="automatic",
                            suggestion_id=s["id"],
                            kind=s["kind"],
                            experimental=evaluation_mode,
                        )
                    )
            if latest["cuts"] != before["cuts"]:
                latest["undo"] = (latest["undo"] + [before])[-100:]
                latest["revision"] += 1
                latest["history"].append(
                    dict(
                        action="automatic_clean",
                        at=storage.now(),
                        policy=POLICY_VERSION,
                    )
                )
            timeline = build_timeline(
                job["media"]["duration"], latest["cuts"], job["media"]["fps"]
            )
            jobs.write(job_id, "edit.json", latest)
            jobs.write(job_id, "suggestions.json", suggestions)
            revision = latest["revision"]
            report = dict(
                schema=1,
                policy=POLICY_VERSION,
                policy_hash=self.policy_hash,
                revision=revision,
                transcript_hash=digest(transcript),
                provider=provider,
                applied=len(latest["cuts"]),
                uncertain=sum(not s["auto"] for s in suggestions),
                source_seconds=job["media"]["duration"],
                output_seconds=timeline["duration"],
                removed_seconds=job["media"]["duration"] - timeline["duration"],
                human_validation=human_gate["state"],
                suggestions=suggestions,
                notes=list(notes_by_id.values()),
                mode=state["options"].get("mode", "standard"),
                background_profile=profile,
                background_voice=voice_state,
                contextual=context_report,
                sequence_audit=sequence_audit,
                contextual_refinement=refinement,
                coherence="strict_replacements_preserve_original_order",
                performance=dict(
                    preparation=job.get("timings"),
                    asr=transcript.get("actual_config"),
                ),
            )
            applied = []
            for cut in latest["cuts"]:
                suggestion = next(
                    (
                        s
                        for s in suggestions
                        if s["id"] == cut.get("suggestion_id")
                    ),
                    None,
                )
                if not suggestion or cut["origin"] != "automatic":
                    continue
                start, end = build_timeline(
                    job["media"]["duration"], [cut], job["media"]["fps"]
                )["cuts"][0]
                words = [
                    w for seg in transcript["segments"] for w in seg["words"]
                ]
                applied.append(
                    dict(
                        id=suggestion["id"],
                        kind=suggestion["kind"],
                        start=start,
                        end=end,
                        join_output_seconds=source_to_output(timeline, start),
                        reason=suggestion["reason"],
                        before=" ".join(
                            w["text"]
                            for w in [w for w in words if w["end"] <= start][
                                -8:
                            ]
                        ),
                        after=" ".join(
                            w["text"]
                            for w in [w for w in words if w["start"] >= end][:8]
                        ),
                        replacement=suggestion["evaluation"].get(
                            "editorial", {}
                        ),
                        intact=[start, end]
                        == build_timeline(
                            job["media"]["duration"],
                            [suggestion],
                            job["media"]["fps"],
                        )["cuts"][0],
                    )
                )
            report["applied_cuts"] = applied
            report["result"] = "cuts_applied" if applied else "no_safe_cuts"
            report["message"] = (
                "Edição experimental pronta para avaliação."
                if applied and evaluation_mode
                else (
                    "Cortes aplicados."
                    if applied
                    else "Nenhum corte seguro encontrado"
                )
            )
            if state["options"]["send_text"] and provider != "complete":
                report["message"] = (
                    "Assistência editorial indisponível; limpeza local concluída. "
                    + report["message"]
                )
            report["contextual_applied"] = sum(
                c["kind"] == "contextual_restart" for c in applied
            )
            report["contextual_result"] = (
                "not_requested"
                if not state["options"]["send_text"]
                else "not_available"
                if provider != "complete"
                else "cuts_applied"
                if report["contextual_applied"]
                else "no_safe_restarts"
            )
            if report["contextual_result"] == "no_safe_restarts":
                report["message"] += (
                    " Nenhum reinício contextual seguro encontrado."
                )
            report["editorial_delivery"] = (
                "context_checked" if provider == "complete" else "local_cleanup"
            )
            jobs.write(job_id, "automatic_report.json", report)
            jobs.write(
                job_id,
                "automatic/checkpoint.json",
                dict(
                    policy_hash=self.policy_hash,
                    input_signature=state.get("input_signature"),
                    revision=revision,
                    edit_hash=digest(latest),
                    transcript_hash=digest(transcript),
                    report_hash=digest(report),
                ),
            )
        return revision

    def _export_if_requested(self, job_id, revision, state, stage, timings):
        if state["options"]["export"]:
            stage("exporting")
            tick = time.monotonic()
            item = next(
                (
                    e
                    for e in self.exports.list(job_id)
                    if e["state"] == "complete" and e["status"] == "current"
                ),
                None,
            )
            if item:
                stage("exporting", render_id=item["render_id"], reused_export=True)
            else:
                item = self.exports.request(job_id, revision, enqueue=False)
                stage("exporting", render_id=item["render_id"], reused_export=False)
                self.exports.execute(job_id, item["render_id"])
            timings["export"] = time.monotonic() - tick

    def run(self, job_id):
        jobs = self.jobs
        state = jobs.read(job_id, "automatic.json")
        evaluation_mode = state["options"].get("mode", "standard") == "evaluation"
        started = time.monotonic()
        timings = {}

        def stage(name, **extra):
            state.update(state=name, timings=dict(timings), **extra)
            jobs.write(job_id, "automatic.json", state)

        try:
            job, edit, transcript, revision, refinement = self._prepare_run(
                job_id, state, stage
            )
            resume = state.get("resume_revision")
            voice_state = dict(state="not_available")
            if resume is None:
                stage("analyzing")
                tick = time.monotonic()
                found, suggestions, profile, voice_state = self._detect_candidates(
                    job_id, job, edit, transcript
                )
                (
                    suggestions,
                    editorial_protected,
                    context_report,
                    notes_by_id,
                    provider,
                ) = self._resolve_contextual_and_editorial(
                    job_id, job, transcript, edit, found, suggestions, state, stage
                )
                (
                    suggestions,
                    human_gate,
                    sequence_audit,
                    allowed,
                ) = self._validate_and_refine(
                    job_id,
                    job,
                    transcript,
                    edit,
                    suggestions,
                    notes_by_id,
                    editorial_protected,
                    context_report,
                    evaluation_mode,
                    state,
                    stage,
                )
                if state["options"]["send_text"]:
                    provider = (
                        "complete"
                        if context_report["state"] == "complete"
                        and sequence_audit["state"] in ("complete", "not_needed")
                        else "unavailable_or_invalid"
                    )
                timings["analysis"] = time.monotonic() - tick
                revision = self._apply_cuts_and_report(
                    job_id,
                    job,
                    transcript,
                    revision,
                    suggestions,
                    notes_by_id,
                    human_gate,
                    allowed,
                    provider,
                    profile,
                    voice_state,
                    context_report,
                    sequence_audit,
                    refinement,
                    state,
                    evaluation_mode,
                )
                stage("analyzing", applied_revision=revision)
                if state["options"]["send_text"] and provider != "complete":
                    # The name must not claim context the provider never delivered.
                    name = jobs.info(job_id)["name"]
                    if name.endswith(CONTEXT_SUFFIX):
                        jobs.update(
                            job_id,
                            name=name[: -len(CONTEXT_SUFFIX)] + CONTEXT_UNAVAILABLE_SUFFIX,
                        )

            self._export_if_requested(job_id, revision, state, stage, timings)
            timings["total"] = time.monotonic() - started
            stage("complete", completed_at=storage.now())
        except Exception as error:
            stage("failed", error=str(error))
            raise
