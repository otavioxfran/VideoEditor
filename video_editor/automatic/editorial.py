"""Editorial packet construction and validation."""
import json

from video_editor.cuts import tokens
from .bounds import overlap

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
