"""Five actual audiovisual reviews unlock only strict replacement classes."""

from pathlib import Path
from video_editor import storage
from video_editor.timeline import digest
from video_editor.editing import require_revision, load_edit


def policy_hash():
    root = Path(__file__).parent
    sources = {
        name: (root / name).read_text(encoding="utf-8")
        for name in (
            "automatic.py",
            "automatic_evaluation.py",
            "contextual.py",
            "noise_profile.py",
            "models.py",
            "cuts.py",
            "cut_policy.py",
            "activity.py",
            "transcription.py",
            "timeline.py",
            "pipeline.py",
            "media.py",
            "export/renderer.py",
            "export/service.py",
            "editing.py",
            "editorial/llm_client.py",
            "acceptance.py",
            "channel_profile.py",
            "pacing.py",
            "takes.py",
        )
    }
    persona = root.parent / "PERSONA.md"
    sources["author_style"] = (
        persona.read_text(encoding="utf-8") if persona.exists() else ""
    )
    # Learned thresholds change cuts as much as code does; feedback logs do not.
    from video_editor.channel_profile import decision_parameters

    sources["channel_profile"] = decision_parameters()
    return digest(sources)


def record(jobs, job_id, request):
    with jobs.lock(job_id):
        jobs.validate(job_id)
        _, edit, transcript = load_edit(jobs, job_id)
        require_revision(edit, request["expected_revision"])
        report = jobs.read(job_id, "automatic_report.json")
        if report.get("transcript_hash") is not None and report[
            "transcript_hash"
        ] != digest(transcript):
            raise ValueError("Relatório não corresponde à transcrição atual.")
        if report.get("policy_hash") != policy_hash():
            raise ValueError(
                "O motor mudou após esta análise; reavalie a edição antes de aprovar."
            )
        automatic_path = jobs.path(job_id, "automatic.json")
        if automatic_path.exists():
            state = storage.read(automatic_path)
            if state["state"] != "complete":
                raise ValueError(
                    "Aguarde a conclusão da edição antes de registrar a avaliação."
                )
        from video_editor.export.service import Exports
        from video_editor.worker import Worker

        exports = Exports(jobs, Worker())
        output = next(
            (
                e
                for e in exports.list(job_id)
                if e["state"] == "complete" and e["status"] == "current"
            ),
            None,
        )
        if output is None:
            raise ValueError("A avaliação exige o MP4 concluído da edição atual.")
        if report["revision"] != edit["revision"]:
            raise ValueError("Revise a versão atual antes de registrar a avaliação.")
        applied = report.get("applied_cuts", [])
        expected = {s["id"] for s in applied}
        actual_ids = {
            c.get("suggestion_id") for c in edit["cuts"] if c["origin"] == "automatic"
        }
        if expected != actual_ids:
            raise ValueError(
                "Relatório de cortes não corresponde à edição atual; reavalie."
            )
        labels = request["labels"]
        if set(labels) != expected or any(
            v not in ("correct", "keep", "uncertain") for v in labels.values()
        ):
            raise ValueError(
                "Avalie todos os cortes efetivamente aplicados nessa versão."
            )
        if request["watched_complete"] is not True:
            raise ValueError("A avaliação exige assistir ao vídeo completo com áudio.")
        value = dict(
            schema=2,
            created_at=storage.now(),
            job_id=job_id,
            policy_hash=policy_hash(),
            source=jobs.info(job_id)["source"],
            report_hash=digest(report),
            revision=edit["revision"],
            labels=labels,
            applied_cuts=applied,
            severe_errors=request["severe_errors"],
            naturalness=request["naturalness"],
            missed_errors=request["missed_errors"],
            correction_minutes=request["correction_minutes"],
            watched_complete=True,
            role="human",
            approved=request["approved"],
            render_id=output["render_id"],
            render_identity=output["identity"],
        )
        jobs.write(job_id, "human_acceptance.json", value)
        return value


def gate(jobs):
    reviews = []
    for job in jobs.list():
        path = jobs.directory(job["job_id"]) / "human_acceptance.json"
        if not path.exists():
            continue
        try:
            review = storage.read(path)
            report = jobs.read(job["job_id"], "automatic_report.json")
            edit = jobs.read(job["job_id"], "edit.json")
            jobs.validate(job["job_id"])
            if (
                review["policy_hash"] != policy_hash()
                or report.get("policy_hash") != policy_hash()
                or review["report_hash"] != digest(report)
                or review["revision"] != edit["revision"]
                or not review["watched_complete"]
                or review["role"] != "human"
                or review.get("schema") != 2
                or review.get("applied_cuts") != report.get("applied_cuts", [])
            ):
                continue
            from video_editor.export.service import Exports
            from video_editor.worker import Worker

            output = Exports(jobs, Worker()).get(job["job_id"], review["render_id"])
            if (
                output["status"] != "current"
                or output["identity"] != review["render_identity"]
            ):
                continue
            reviews.append(review)
        except (ValueError, OSError, KeyError):
            continue
    distinct = {}
    for review in sorted(reviews, key=lambda r: r["created_at"]):
        distinct[review["source"]["path"]] = review
    latest = sorted(distinct.values(), key=lambda r: r["created_at"])[-5:]
    passed = (
        len(latest) == 5
        and len({r["source"]["path"] for r in latest}) == 5
        and all(
            r["approved"] and r["severe_errors"] == 0 and r["naturalness"] >= 4
            for r in latest
        )
    )
    classes = {}
    for kind in ("retake", "false_start", "correction", "adaptive_silence", "contextual_restart"):
        examples = [
            (r, c)
            for r in latest
            for c in r["applied_cuts"]
            if c["kind"] == kind and c.get("intact") is True
        ]
        examples = list(
            {(r["source"]["path"], c["id"]): (r, c) for r, c in examples}.values()
        )
        correct = [(r, c) for r, c in examples if r["labels"].get(c["id"]) == "correct"]
        sources = {r["source"]["path"] for r, c in correct}
        classes[kind] = dict(
            enabled=passed
            and len(correct) >= 5
            and len(sources) >= 3
            and len(correct) == len(examples),
            correct_cuts=len(correct),
            distinct_videos=len(sources),
            required_cuts=5,
            required_videos=3,
        )
    enabled = [kind for kind, value in classes.items() if value["enabled"]]
    return dict(
        enabled=bool(enabled),
        enabled_classes=enabled,
        classes=classes,
        reviewed=len(latest),
        required=5,
        policy_hash=policy_hash(),
        state="validated_classes" if enabled else "pending_five_videos",
    )
