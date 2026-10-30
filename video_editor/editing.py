import copy
import math
from uuid import uuid4
from video_editor import storage
from video_editor.timeline import build_timeline, digest, interval, source_to_output


class RevisionConflict(ValueError):
    pass


def require_revision(document, expected):
    if document['revision'] != expected:
        raise RevisionConflict("A edição mudou em outra aba. Recarregue antes de aplicar.")


def load_edit(jobs, job_id):
    job = jobs.info(job_id)
    if job.get('legacy'):
        raise ValueError("Migração explícita necessária.")
    edit = jobs.read(job_id, 'edit.json')
    transcript = jobs.read(job_id, 'transcript.json')
    if edit['transcript_hash'] != digest(transcript):
        raise ValueError("Transcrição alterada; correspondência de decisões precisa ser revisada.")
    return job, edit, transcript


DECISION_ACTIONS = {'cut': 'applied', 'restore': 'kept', 'protect': 'protected'}


def decisions(edit):
    """Latest human decision per suggestion, replaying history the way undo unwinds it."""
    # Every committed non-undo action pushes one undo entry and undo pops one, so the
    # surviving history is a stack; restore leaves no trace in the cuts, only here.
    stack = []
    for entry in edit.get('history', []):
        if entry.get('action') == 'undo':
            if stack:
                stack.pop()
        else:
            stack.append(entry)
    result = {}
    for entry in stack:
        sid, action = entry.get('suggestion_id'), entry.get('action')
        if sid and action in DECISION_ACTIONS:
            result[sid] = DECISION_ACTIONS[action]
    # A suggestion cut by the automatic pass or a range edit is applied even without a request.
    for cut in edit.get('cuts', []):
        if cut.get('suggestion_id'):
            result.setdefault(cut['suggestion_id'], 'applied')
    return result


def view(jobs, job_id):
    job, edit, transcript = load_edit(jobs, job_id)
    timeline = build_timeline(job['media']['duration'], edit['cuts'], job['media'].get('fps'))
    # The preview applies the same punch-in plan the export will render.
    from video_editor import framing
    for keep, scale in zip(timeline['keeps'], framing.plan(timeline, transcript, framing.settings())):
        keep['scale'] = scale
    return dict(job=job, edit=edit, transcript=transcript, timeline=timeline,
                suggestions=jobs.read(job_id, 'suggestions.json'), decisions=decisions(edit),
                markers=[dict(m, output_time=source_to_output(timeline, m['source_time'])) for m in edit['markers']])


def subtract(cuts, start, end):
    result = []
    for cut in cuts:
        if cut['end'] <= start or cut['start'] >= end:
            result.append(cut)
        else:
            if cut['start'] < start:
                result.append(dict(cut, end=start))
            if cut['end'] > end:
                result.append(dict(cut, start=end))
    return result


def mutate(jobs, job_id, request):
    with jobs.lock(job_id):
        jobs.validate(job_id)
        job, edit, transcript = load_edit(jobs, job_id)
        require_revision(edit, request['expected_revision'])
        duration = job['media']['duration']
        action = request['action']
        feedback = []
        before ={key: copy.deepcopy(edit[key]) for key in ('cuts', 'markers', 'protected')}
        if action == 'undo':
            if not edit['undo']:
                raise ValueError("Nenhuma ação para desfazer.")
            edit.update(edit['undo'].pop())
        elif action == 'marker':
            time = request.get('source_time')
            if time is None or not math.isfinite(time) or not 0 <= time <= duration:
                raise ValueError("Marcador precisa de tempo na origem.")
            edit['markers'].append(dict(id=uuid4().hex, source_time=time, label=request.get('label', '')))
        elif action == 'delete_marker':
            target = request.get('marker_id')
            if not any(m['id'] == target for m in edit['markers']):
                raise ValueError("Marcador inexistente.")
            edit['markers'] = [m for m in edit['markers'] if m['id'] != target]
        else:
            segment_ids = request.get('segment_ids')
            if segment_ids:
                known = {s['id']: s for s in transcript['segments']}
                if len(set(segment_ids)) != len(segment_ids) or any(s not in known for s in segment_ids):
                    raise ValueError('Referências de segmento inválidas.')
                targets = [(known[s]['start'], known[s]['end']) for s in segment_ids]
            else:
                targets = []
            start, end = request.get('start'), request.get('end')
            sid = request.get('suggestion_id')
            if sid:
                if start is not None or end is not None:
                    raise ValueError("Informe sugestão ou intervalo, não ambos.")
                suggestion = next((s for s in jobs.read(job_id, 'suggestions.json') if s['id'] == sid), None)
                if suggestion is None:
                    raise ValueError("Sugestão inexistente.")
                start, end = suggestion['start'], suggestion['end']
            if not targets and (start is None or end is None):
                raise ValueError("Intervalo obrigatório.")
            for start, end in targets or [(start, end)]:
                start, end = interval(start, end, duration)
                if action == 'cut':
                    edit['cuts'].append(dict(start=start, end=end, origin='human', suggestion_id=sid))
                else:
                    edit['cuts'] = subtract(edit['cuts'], start, end)
                    if action == 'protect':
                        edit['protected'].append(dict(start=start, end=end))
                feedback.append(dict(start=start, end=end, suggestion_id=sid))
        if action != 'undo':
            edit['undo'] = (edit['undo'] + [before])[-100:]
        # Validate before the single atomic commit, including edits that remove all content.
        build_timeline(duration, edit['cuts'], job['media'].get('fps'))
        edit['revision'] += 1
        edit['history'].append(dict(at=storage.now(), **request))
        jobs.write(job_id, 'edit.json', edit)
    # Learn only from committed edits; a rejected request must not move the profile.
    for details in feedback:
        try:
            from video_editor.channel_profile import record_feedback
            record_feedback(action, details)
        except Exception:
            pass
    return view(jobs, job_id)
