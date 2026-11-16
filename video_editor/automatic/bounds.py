"""Frame-safe interval helpers."""
from video_editor.timeline import build_timeline

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
