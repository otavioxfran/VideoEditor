"""Contextual and editorial resolution for Automatic."""
from video_editor.analysis import contextual
from video_editor.timeline import digest
from video_editor.editorial import llm_client
from video_editor.learning.automatic_evaluation import local_decisions
from .classify import classify
from .constants import POLICY_VERSION
from .editorial import editorial_packet, validate_editorial
def resolve_contextual_and_editorial(
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
