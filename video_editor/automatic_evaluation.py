"""Isolated automatic evaluation jobs and conservative local restart evidence."""

import copy
import shutil

from video_editor import storage, media
from video_editor.editing import load_edit, require_revision
from video_editor.safety import source_identity, validate_source
from video_editor.timeline import digest
from video_editor.cuts import tokens, RESETS


def clone_prepared(jobs, job_id, revision):
    with jobs.lock(job_id):
        jobs.validate(job_id)
        job, edit, transcript = load_edit(jobs, job_id)
        require_revision(edit, revision)
        if job["state"] != "ready":
            raise ValueError("Prepare a gravação antes de criar a versão experimental.")
        if not job.get("render_audio"):
            raise ValueError("Este job não tem áudio preparado; prepare um novo job.")
        validate_source(job["render_audio"])
        checkpoint = jobs.read(job_id, "audio/checkpoint.json")
        if (
            checkpoint.get("contract") != 2
            or checkpoint.get("source_hash") != digest(job["source"])
            or checkpoint.get("render_audio") != job["render_audio"]
        ):
            raise ValueError("Checkpoint de áudio incompatível com a gravação.")
        mono = checkpoint.get("mono_audio")
        if mono:
            validate_source(mono)
        child = jobs.create(job["source"]["path"], copy.deepcopy(job["config"]))
        cid = child["job_id"]
        try:
            for relative in [
                "audio/render_pcm.wav",
                "audio/extracted_16k.wav",
                "audio/activity.json",
                "media/preview.mp4",
                "media/checkpoint.json",
            ]:
                source = jobs.path(job_id, relative)
                if relative == "audio/extracted_16k.wav" and not mono:
                    continue
                if source.exists():
                    target = jobs.path(cid, relative)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
            validate_source(job["render_audio"])
            if mono:
                validate_source(mono)
            jobs.validate(job_id)
            pcm = source_identity(jobs.path(cid, "audio/render_pcm.wav"))
            if not mono:
                # Older prepared jobs recorded only the native PCM identity. Rebuild the
                # derived mono in this child rather than trusting or changing that old file.
                media.extract(
                    pcm["path"],
                    jobs.path(cid, "audio/extracted_16k.wav"),
                    jobs.directory(cid),
                    [job["source"]["path"]],
                )
            jobs.write(
                cid,
                "audio/checkpoint.json",
                dict(
                    contract=2,
                    source_hash=digest(child["source"]),
                    render_audio=pcm,
                    mono_audio=source_identity(
                        jobs.path(cid, "audio/extracted_16k.wav")
                    ),
                ),
            )
            jobs.write(cid, "transcript.json", transcript)
            jobs.write(cid, "suggestions.json", jobs.read(job_id, "suggestions.json"))
            # Existing range decisions form this child's baseline and remain protected from replacement.
            baseline = dict(
                schema=1,
                revision=0,
                transcript_hash=digest(transcript),
                cuts=[dict(c, origin="human") for c in edit["cuts"]],
                markers=copy.deepcopy(edit["markers"]),
                protected=copy.deepcopy(edit["protected"]),
                undo=[],
                history=[
                    dict(
                        action="evaluation_copy",
                        at=storage.now(),
                        parent_revision=revision,
                    )
                ],
            )
            jobs.write(cid, "edit.json", baseline)
            return jobs.update(
                cid,
                name=job["name"] + " · Edição experimental",
                state="ready",
                stage="ready",
                editing_mode="evaluation",
                media=copy.deepcopy(job["media"]),
                render_audio=pcm,
                preview=copy.deepcopy(job.get("preview")),
                evaluation_parent=dict(
                    job_id=job_id,
                    revision=revision,
                    cohort="calibration",
                    edit_hash=digest(edit),
                    transcript_hash=digest(transcript),
                ),
                timings=dict(
                    checkpoints_reused=True, mono_rebuilt_from_pcm=not bool(mono)
                ),
            )
        except Exception:
            jobs.update(
                cid,
                state="failed",
                stage="evaluation_copy",
                editing_mode="evaluation",
                error="Cópia dos checkpoints falhou; o job anterior foi preservado.",
            )
            raise


def local_decisions(transcript, candidates):
    """Literal coverage alone isn't enough: a speaker's restart cue is required."""
    segments = transcript["segments"]
    decisions = []
    for candidate in candidates:
        if candidate["kind"] not in ("retake", "false_start"):
            continue
        is_intra = candidate["id"].startswith("intra-") and bool(candidate.get("replacement_word_ids"))
        words = {w["id"]: w for s in segments for w in s["words"]}
        removed_words = [words[i] for i in candidate.get("removed_word_ids", []) if i in words]
        if is_intra:
            probs = [w["probability"] for w in removed_words]
            if len(removed_words) < 2 or sum(probs) / len(probs) < 0.65 or min(probs) < 0.25:
                continue
            rem_tokens = tokens(" ".join(w["text"] for w in removed_words))
            if any(t in ("nunca", "jamais", "sem", "nem") or any(c.isdigit() for c in t) for t in rem_tokens):
                continue
            replacements = candidate.get("replacement_word_ids", [])
            rep_tokens = tokens(" ".join(words[wid]["text"] for wid in replacements if wid in words))
            if "nao" in rem_tokens and "nao" not in rep_tokens:
                continue
            decisions.append(
                dict(
                    suggestion_id=candidate["id"],
                    action="remove",
                    replacement_segment_ids=[],
                    replacement_word_ids=replacements,
                    information_loss=False,
                    coherent_join=True,
                    reason="Repetição literal idêntica dentro da fala com palavras confiáveis.",
                    origin="local",
                )
            )
            continue

        preceding = [s for s in segments if s["end"] <= candidate["start"] + 0.02]
        removed = [
            s
            for s in segments
            if s["start"] < candidate["end"] and s["end"] > candidate["start"]
        ]
        previous = preceding[-1] if preceding else None
        cue = bool(
            previous
            and candidate["start"] - previous["end"] <= 5
            and " ".join(tokens(previous["text"])) in RESETS
        )
        last = removed[-1]["text"].rstrip() if removed else ""
        tail = tokens(last)
        abandoned = last.endswith("-") or bool(
            last.endswith(("...", "…"))
            and tail
            and tail[-1]
            in ("de", "do", "da", "dos", "das", "com", "para", "sobre", "pelo", "pela")
        )
        if not (cue or abandoned):
            continue
        if not removed_words:
            removed_words = [w for s in removed for w in s["words"]]
        if not removed_words:
            continue
        p_vals = [w["probability"] for w in removed_words]
        if sum(p_vals) / len(p_vals) < 0.65 or min(p_vals) < 0.25:
            continue
        replacements = candidate.get("replacement_word_ids", [])
        target = next((s for s in segments if s["start"] >= candidate["end"]), None)
        decisions.append(
            dict(
                suggestion_id=candidate["id"],
                action="remove",
                replacement_segment_ids=[]
                if replacements or target is None
                else [target["id"]],
                replacement_word_ids=replacements,
                information_loss=False,
                coherent_join=True,
                reason="Reinício explícito do locutor; substituição literal ainda exige validação local.",
                origin="local",
            )
        )
    return decisions
