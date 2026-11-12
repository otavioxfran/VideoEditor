import time
from concurrent.futures import ThreadPoolExecutor
from video_editor import activity, media, storage, transcription
from video_editor.cuts import find_candidates
from video_editor.cut_policy import apply_policy
from video_editor.timeline import digest, interval
from video_editor.safety import source_identity, validate_source
from video_editor.settings import user_settings


class Pipeline:
    def __init__(self, jobs):
        self.jobs = jobs

    def prepare(self, job_id):
        jobs = self.jobs
        started = time.monotonic()
        timings = {}

        def stage(name):
            jobs.update(job_id, state='processing', stage=name, error=None, timings=dict(timings))
            return time.monotonic()

        try:
            source = jobs.validate(job_id)
            job = jobs.info(job_id)
            tick = stage('media')
            info = media.probe(source)
            jobs.update(job_id, media=info)
            timings['media'] = time.monotonic() - tick
            tick = stage('audio')
            wav = jobs.path(job_id, 'audio/extracted_16k.wav')
            checkpoint = jobs.path(job_id, 'audio/checkpoint.json')
            signature = digest(job['source'])
            render_audio=jobs.path(job_id,'audio/render_pcm.wav')
            cached=storage.read(checkpoint) if checkpoint.exists() else {}
            valid_audio=(render_audio.exists() and cached.get('source_hash')==signature and cached.get('contract')==2)
            if valid_audio:
                validate_source(cached['render_audio'])
            else:
                media.extract_render_audio(source,render_audio,jobs.directory(job_id),
                    [j['source']['path'] for j in jobs.list() if 'source' in j],origin=info.get('origin',0))
            valid_mono=wav.exists() and valid_audio and cached.get('mono_audio')
            if valid_mono:
                validate_source(cached['mono_audio'])
            if not valid_mono:
                media.extract(render_audio,wav,jobs.directory(job_id),[source])
            audio_identity=source_identity(render_audio)
            jobs.write(job_id,'audio/checkpoint.json',dict(source_hash=signature,contract=2,render_audio=audio_identity,
                mono_audio=source_identity(wav)))
            jobs.update(job_id,render_audio=audio_identity)
            timings['audio'] = time.monotonic() - tick
            tick = stage('transcription')
            # Acoustic analysis is CPU-bound and shares the extracted audio checkpoint.
            with ThreadPoolExecutor(max_workers=1) as cpu:
                activity_future=cpu.submit(activity.detect,wav,info['duration'],render_audio)
                transcript, asr_seconds = self._transcript(job_id,job,wav,signature,info)
                timings['asr']=asr_seconds
                tick=stage('activity')
                silences=activity_future.result()
                jobs.write(job_id,'audio/activity.json',silences)
                timings['activity_wait']=time.monotonic()-tick
            if media.needs_proxy(info,source):
                tick=stage('preview')
                proxy=jobs.path(job_id,'media/preview.mp4')
                proxy_checkpoint=jobs.path(job_id,'media/checkpoint.json')
                identity=digest(dict(source=job['source'],media=info,contract=2))
                if not (proxy.exists() and proxy_checkpoint.exists() and storage.read(proxy_checkpoint).get('identity')==identity):
                    media.preview_proxy(source,proxy,jobs.directory(job_id),dict(info,render_audio_path=str(render_audio)),
                        [j['source']['path'] for j in jobs.list() if 'source' in j])
                    jobs.write(job_id,'media/checkpoint.json',dict(identity=identity))
                jobs.update(job_id,preview=dict(path='media/preview.mp4',identity=identity,
                    source_clock='video_origin',origin=info.get('origin',0),fps=info['fps']))
                timings['preview']=time.monotonic()-tick
            tick = stage('analysis')
            suggestions = apply_policy(transcript, find_candidates(transcript), info['duration'], info['fps'])
            suggestions += activity.silence_candidates(transcript, silences, info['duration'])
            suggestions += activity.adaptive_for_job(jobs,job_id,transcript,info['duration'])
            from video_editor.channel_profile import detect_speech_gaps
            suggestions += detect_speech_gaps(transcript, info['duration'], wav=wav)
            from video_editor import takes
            from video_editor.channel_profile import load_profile
            proposals = takes.candidates(transcript)
            if not load_profile().get('auto_cut_takes', True):
                for proposal in proposals:
                    proposal['auto'] = False
            suggestions = takes.merge(suggestions, proposals)
            # Silence bounds are also validated after alignment, against all words.
            from video_editor.automatic import safe_bounds
            for s in suggestions:
                if s['auto']:
                    bounds=safe_bounds(s,transcript,info['duration'],info['fps'])
                    if bounds:
                        s.update(bounds)
                    else:
                        s.update(auto=False,reason='Limites de frame exigem revisão; fala preservada.')
            from video_editor import pacing
            pacing.seal(pacing.thin(pacing.reconcile(suggestions)), transcript)
            takes.guard(suggestions, transcript)
            suggestions.sort(key=lambda s: (s['start'], s['end']))
            with jobs.lock(job_id):
                jobs.validate(job_id)
                edit_path = jobs.path(job_id, 'edit.json')
                if edit_path.exists():
                    if storage.read(edit_path)['transcript_hash'] != digest(transcript):
                        raise ValueError("Edição existente não corresponde à transcrição.")
                else:
                    cuts = [dict(start=s['start'], end=s['end'], origin='mechanical', suggestion_id=s['id'])
                            for s in suggestions if s['auto']]
                    jobs.write(job_id, 'edit.json', dict(schema=1, revision=0, transcript_hash=digest(transcript),
                               cuts=cuts, markers=[], protected=[], undo=[], history=[]))
                jobs.write(job_id, 'suggestions.json', suggestions)
            timings['analysis'] = time.monotonic() - tick
            timings['total'] = time.monotonic() - started
            return jobs.update(job_id, state='ready', stage='ready', timings=timings, error=None)
        except Exception as error:
            jobs.update(job_id, state='failed', error=str(error), timings=timings)
            raise

    def _transcript(self,job_id,job,wav,signature,info):
        jobs=self.jobs
        tick=time.monotonic()
        transcript_path = jobs.path(job_id, 'transcript.json')
        if transcript_path.exists():
            transcript = storage.read(transcript_path)
            if transcript['source_hash'] != signature or transcript.get('requested_config') != job['config']:
                raise ValueError("Checkpoint ASR incompatível; crie outro job para outra configuração.")
        else:
            from video_editor import glossary
            screen = (
                glossary.screen_terms(
                    job['source']['path'],
                    info['duration'],
                    cache_path=jobs.path(job_id, 'visual/screen_terms.json'),
                )
                if glossary.ocr_available()
                else dict(state='unavailable', terms=[])
            )
            user = glossary.user_terms()
            terms = user + list(screen.get('terms') or [])
            hints = glossary.hotwords(terms)
            enabled = bool(user_settings().get('glossary_hotwords', False))
            if hints and enabled:
                transcript, actual = transcription.transcribe(
                    wav, job['config'], job['source'], hotwords=hints
                )
            else:
                transcript, actual = transcription.transcribe(
                    wav, job['config'], job['source']
                )
            for segment in transcript['segments']:
                interval(segment['start'], segment['end'], info['duration'])
            transcript.update(requested_config=job['config'], actual_config=actual)
            if terms:
                transcript['glossary'] = dict(
                    screen_terms=list(screen.get('terms') or []),
                    user_terms=user,
                    hotwords=hints,
                    hotwords_used=bool(hints and enabled),
                )
            jobs.write(job_id, 'transcript.json', transcript)
        return transcript,time.monotonic()-tick
