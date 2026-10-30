from pathlib import Path
from uuid import uuid4
from video_editor import storage
from video_editor.editing import load_edit, require_revision
from video_editor.export.renderer import DEFAULT_CONFIG, render
from video_editor.export.subtitles import srt
from video_editor.timeline import build_timeline, export_identity,digest
from video_editor.safety import validate_source


class Exports:
    def __init__(self, jobs, worker, render_function=render, config=None):
        self.jobs, self.worker, self.render_function = jobs, worker, render_function
        self.config = dict(config or DEFAULT_CONFIG)

    def current_config(self):
        # Punch-in is a creator preference: read live, and part of the export identity,
        # so toggling it marks earlier exports stale.
        from video_editor import framing
        return dict(self.config, punch_in=framing.settings()) if 'punch_in' not in self.config else dict(self.config)

    def identity(self, job_id):
        job, edit, transcript = load_edit(self.jobs, job_id)
        self.jobs.validate(job_id)
        timeline = build_timeline(job['media']['duration'], edit['cuts'], job['media'].get('fps'))
        return export_identity(job, transcript, timeline, self.current_config())

    def request(self, job_id, revision, enqueue=True):
        jobs = self.jobs
        with jobs.lock(job_id):
            jobs.validate(job_id)
            job, edit, transcript = load_edit(jobs, job_id)
            require_revision(edit, revision)
            timeline = build_timeline(job['media']['duration'], edit['cuts'], job['media'].get('fps'))
            if not timeline['keeps']:
                raise ValueError('Não há conteúdo mantido para exportar.')
            render_id = uuid4().hex
            config = self.current_config()
            snapshot = dict(schema=1, job_id=job_id, render_id=render_id, revision=revision,
                            created_at=storage.now(), source=job['source'], media=job['media'],
                            transcript=transcript, timeline=timeline, config=config)
            snapshot['audio_source']=job.get('render_audio')
            snapshot['mode']=job.get('editing_mode','standard')
            snapshot['protected_sources'] = [j['source']['path'] for j in jobs.list() if 'source' in j]
            snapshot['identity'] = export_identity(job, transcript, timeline, config)
            prefix = f'exports/{render_id}'
            jobs.write(job_id, prefix + '/snapshot.json', snapshot)
            metadata = dict(render_id=render_id, state='queued', created_at=snapshot['created_at'],
                            revision=revision, mode=snapshot['mode'], identity=snapshot['identity'],snapshot_digest=digest(snapshot), error=None)
            jobs.write(job_id, prefix + '/export.json', metadata)
        if not enqueue:
            return metadata
        try:
            self.worker.submit(self.execute, job_id, render_id)
        except Exception as error:
            jobs.write(job_id, prefix + '/export.json', dict(metadata, state='failed', error=str(error)))
            raise
        return metadata

    def execute(self, job_id, render_id):
        jobs = self.jobs
        prefix = f'exports/{render_id}'
        snapshot = jobs.read(job_id, prefix + '/snapshot.json')
        metadata = jobs.read(job_id, prefix + '/export.json')
        try:
            if metadata.get('snapshot_digest')!=digest(snapshot):
                raise ValueError('Snapshot foi alterado ou não tem assinatura completa; solicite outra exportação.')
            validate_source(snapshot['source'])
            if snapshot.get('audio_source'):validate_source(snapshot['audio_source'])
            identity = export_identity({'source': snapshot['source'],'render_audio':snapshot.get('audio_source')}, snapshot['transcript'],
                                       snapshot['timeline'], snapshot['config'])
            if identity != snapshot['identity'] or identity != metadata['identity']:
                raise ValueError('Snapshot foi alterado; exportação cancelada.')
            jobs.write(job_id, prefix + '/export.json', dict(metadata, state='rendering'))
            output = jobs.path(job_id, prefix + '/video.mp4')
            result = self.render_function(snapshot, output, jobs.directory(job_id))
            # Publish both files only when the source is unchanged and MP4 is validated.
            validate_source(snapshot['source'])
            if snapshot.get('audio_source'):validate_source(snapshot['audio_source'])
            storage.write_text(jobs.path(job_id, prefix + '/subtitles.srt'),
                               srt(snapshot['transcript'], snapshot['timeline']))
            metadata.update(state='complete', completed_at=storage.now(), **result)
            jobs.write(job_id, prefix + '/export.json', metadata)
            return self.get(job_id, render_id)
        except Exception as error:
            jobs.write(job_id, prefix + '/export.json', dict(metadata, state='failed', error=str(error)))
            raise

    def get(self, job_id, render_id):
        # The identifier is always a single component; no traversal through render_id.
        from video_editor.core.security import assert_safe_job_id
        assert_safe_job_id(render_id)
        metadata = self.jobs.read(job_id, f'exports/{render_id}/export.json')
        if metadata['state'] == 'complete':
            try:
                identity = self.identity(job_id)
                metadata['status'] = 'current' if identity == metadata['identity'] else 'stale'
                if metadata.get('snapshot_digest'):
                    snapshot=self.jobs.read(job_id,f'exports/{render_id}/snapshot.json')
                    if digest(snapshot)!=metadata['snapshot_digest']:
                        metadata['status']='stale'
            except (ValueError, OSError, KeyError):
                metadata['status'] = 'stale'
            if not all(self.jobs.path(job_id, f'exports/{render_id}/{name}').is_file()
                       for name in ('video.mp4', 'subtitles.srt')):
                metadata['status'] = 'missing'
        else:
            metadata['status'] = metadata['state']
        return metadata

    def list(self, job_id):
        directory = self.jobs.directory(job_id)
        result = []
        exports_dir = directory / 'exports'
        if exports_dir.exists():
            for export in exports_dir.iterdir():
                if export.is_dir() and (export / 'export.json').is_file():
                    result.append(self.get(job_id, export.name))
        # Legacy media is discoverable, never silently promoted to a current export.
        legacy_dir = directory / 'output'
        if legacy_dir.exists():
            for path in legacy_dir.glob('*.mp4'):
                result.append(dict(render_id=None, filename=path.name, state='historical',
                                   status='historical', compatibility='unknown'))
        return sorted(result, key=lambda x: x.get('created_at', ''), reverse=True)
