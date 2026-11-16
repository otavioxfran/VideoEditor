"""Background-voice candidate generation."""
from video_editor import voice_presence
from video_editor.timeline import build_timeline

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
