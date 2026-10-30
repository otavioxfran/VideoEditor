"""Job persistence and explicit, non-destructive legacy migration."""
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from video_editor import storage
from video_editor.core.job_lock import job_lock
from video_editor.core.security import assert_safe_write_path
from video_editor.safety import destination, job_directory, source_identity, validate_source
from video_editor.settings import ALLOWED_VIDEO_EXTENSIONS
from video_editor.timeline import digest


def file_digest(path):
    import hashlib
    result=hashlib.sha256()
    with open(path,'rb') as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b''):
            result.update(chunk)
    return result.hexdigest()


class Jobs:
    def __init__(self, root, source_dir):
        self.root, self.source_dir = Path(root).resolve(), Path(source_dir).resolve()
        if self.root == self.source_dir or self.root.is_relative_to(self.source_dir):
            raise PermissionError("Workspace precisa ficar fora da filmoteca.")
        self._sources_cache = None

    def protected_sources(self):
        if self._sources_cache is None:
            self._sources_cache = [j['source']['path'] for j in self.list() if 'source' in j]
        return self._sources_cache

    def directory(self, job_id):
        return job_directory(self.root, job_id)

    def runtime_lock(self):
        assert_safe_write_path(self.root, 'workspace local')
        self.root.mkdir(parents=True, exist_ok=True)
        destination(self.root / '.job.lock', self.root, self.protected_sources())
        runtime_id = 'runtime_' + digest(str(self.root))[:16]
        return job_lock(SimpleNamespace(job_id=runtime_id, job_dir=self.root), timeout=.5)

    def path(self, job_id, relative):
        directory = self.directory(job_id)
        # A raw registered outside the source folder receives the same write protection.
        return destination(directory / relative, directory, self.protected_sources())

    def lock(self, job_id):
        directory = self.directory(job_id)
        destination(directory / '.job.lock', directory, self.protected_sources())
        return job_lock(SimpleNamespace(job_id=job_id, job_dir=directory))

    def read(self, job_id, relative):
        return storage.read(self.path(job_id, relative))

    def write(self, job_id, relative, value):
        return storage.write(self.path(job_id, relative), value)

    def info(self, job_id):
        directory = self.directory(job_id)
        if (directory / 'job.json').is_file():
            return storage.read(directory / 'job.json')
        return self.legacy_info(directory)

    def list(self):
        result = []
        if self.root.exists():
            for directory in self.root.iterdir():
                if directory.is_dir() and not directory.is_symlink():
                    try:
                        info = (storage.read(directory / 'job.json') if (directory / 'job.json').is_file()
                                else self.legacy_info(directory))
                        result.append(info)
                    except (FileNotFoundError, ValueError, KeyError, OSError):
                        continue
        ordered = sorted(result, key=lambda x: x.get('created_at', ''), reverse=True)
        self._sources_cache = [j['source']['path'] for j in ordered if 'source' in j]
        return ordered

    def sources(self):
        if not self.source_dir.exists():
            return []
        return [dict(path=str(p.resolve()), name=p.name, size=p.stat().st_size)
                for p in sorted(self.source_dir.iterdir())
                if p.is_file() and p.suffix.lower() in ALLOWED_VIDEO_EXTENSIONS]

    def create(self, source_path, config, allow_external=False):
        identity = source_identity(source_path)
        path = Path(identity['path'])
        if path.suffix.lower() not in ALLOWED_VIDEO_EXTENSIONS:
            raise ValueError('Selecione uma gravação MP4, MOV ou MKV.')
        if path.is_relative_to(self.root):
            raise PermissionError("Selecione um raw fora do workspace de jobs.")
        if not allow_external and not path.is_relative_to(self.source_dir):
            raise PermissionError(f"Gravações da biblioteca web devem estar dentro de {self.source_dir}.")
        assert_safe_write_path(self.root, 'workspace local')
        self.root.mkdir(parents=True, exist_ok=True)
        job_id = uuid4().hex
        directory = destination(self.root / job_id, self.root, [path])
        directory.mkdir(exist_ok=False)
        info = dict(schema=1, job_id=job_id, name=path.stem, source=identity,
                    created_at=storage.now(), config=config, state='created', stage='created',
                    error=None, media=None, timings={})
        self.write(job_id, 'job.json', info)
        if self._sources_cache is not None and identity['path'] not in self._sources_cache:
            self._sources_cache.append(identity['path'])
        return info

    def update(self, job_id, **changes):
        with self.lock(job_id):
            info = self.info(job_id)
            info.update(changes)
            info['updated_at'] = storage.now()
            self.write(job_id, 'job.json', info)
            return info

    def validate(self, job_id):
        info = self.info(job_id)
        if info.get('legacy'):
            raise ValueError("Migre este job antes de editar ou exportar.")
        return validate_source(info['source'])

    def legacy_info(self, directory):
        source = storage.read(directory / 'source_info.json')
        path = source['source_path']
        return dict(schema=0, job_id=directory.name, name=Path(path).stem, legacy=True,
                    source=dict(path=path, size=source.get('source_size_bytes', source.get('source_size')),
                                mtime=source.get('mtime')), created_at=source.get('created_at', ''),
                    state='legacy', stage='legacy', error=None, media=None)

    def migrate(self, job_id):
        with self.lock(job_id):
            info = self.info(job_id)
            if not info.get('legacy'):
                return info
            identity = source_identity(info['source']['path'])
            old = info['source']
            if old.get('size') is None or identity['size'] != old['size']:
                raise ValueError("Identidade legada da fonte insuficiente ou alterada.")
            verification='recorded_size_mtime'
            if old.get('mtime') is None:
                # Early SAFE_COPY jobs lack mtime. Compare raw AND the explicitly registered
                # copy; a hash of the copy alone is not proof of the original's identity.
                original_info=self.read(job_id,'source_info.json')
                copied=original_info.get('copied_path')
                if not copied or not Path(copied).is_file() or Path(copied).stat().st_size != identity['size']:
                    raise ValueError('Fonte legada sem mtime e sem cópia verificável; prepare um novo job.')
                copy_hash=file_digest(copied)
                if file_digest(identity['path']) != copy_hash:
                    raise ValueError('Raw diverge da cópia legada; migração bloqueada.')
                if original_info.get('sha256') and original_info['sha256'] != copy_hash:
                    raise ValueError('Cópia legada foi alterada; migração bloqueada.')
                verification='raw_copy_sha256'
            elif abs(identity['mtime_ns'] / 1e9 - old['mtime']) > .001:
                raise ValueError("Fonte legada alterada.")
            decisions = self.read(job_id, 'analysis/decisions.json')
            from video_editor.transcription import normalize
            transcript = normalize([dict(id=str(s['id']), start=s['start_time'], end=s['end_time'],
                                         text=s['transcript'], words=s.get('words', []))
                                    for s in decisions['segments']], identity, allow_imprecise=True)
            duration = float(decisions['metadata']['duration'])
            cuts, suggestions, protected, markers = [], [], [], []
            # Human overrides are authority. Old automatic proposals are NOT promoted.
            overrides = {str(k): {'action': v} for k, v in decisions.get('user_overrides', {}).items()}
            for segment in decisions['segments']:
                sid = str(segment['id'])
                item = dict(id='legacy-segment-' + sid, start=segment['start_time'], end=segment['end_time'],
                            kind='legacy', reason=segment.get('cut_reason', 'Proposta antiga sem validação.'),
                            auto=False, evidence=[sid])
                override = overrides.get(sid, {})
                action = str(override.get('action', '')).upper()
                if action in ('CUT', 'CUT_AUTO'):
                    cuts.append(dict(start=item['start'], end=item['end'], origin='human',
                                     suggestion_id=item['id']))
                elif action in ('KEEP', 'CREATIVE_PAUSE'):
                    protected.append(dict(start=item['start'], end=item['end']))
                if segment.get('status') == 'CUT_AUTO' or segment.get('category') in ('POSSIBLE_CUT', 'CUT_AUTO'):
                    suggestions.append(item)
            for silence in decisions.get('silences', []):
                sid = str(silence.get('id'))
                action = str(overrides.get('silence_' + sid, {}).get('action', '')).upper()
                start = silence.get('start_time', silence.get('start'))
                end = silence.get('end_time', silence.get('end'))
                if action in ('CUT', 'COMPRESS') and start is not None and end is not None:
                    # Preserve the precise legacy compression range, not the entire pause.
                    left, right = silence.get('cut_start_time'), silence.get('cut_end_time')
                    if left is not None and right is not None and right > left:
                        cuts.append(dict(start=left, end=right, origin='human'))
                elif action in ('KEEP', 'CREATIVE_PAUSE') and start is not None and end is not None:
                    protected.append(dict(start=start, end=end))
            marker_file = self.directory(job_id) / 'analysis/markers.json'
            if marker_file.exists():
                values = storage.read(marker_file)
                values = values.get('markers', []) if isinstance(values, dict) else values
                for value in values:
                    time = value.get('source_time', value.get('time'))
                    if time is not None and 0 <= float(time) <= duration:
                        markers.append(dict(id=uuid4().hex, source_time=float(time),
                                            label=value.get('label', value.get('note', ''))))
            edit = dict(schema=1, revision=0, transcript_hash=digest(transcript), cuts=cuts,
                        protected=protected, markers=markers, undo=[], history=[dict(action='legacy_import', at=storage.now())])
            from video_editor.timeline import build_timeline
            build_timeline(duration, cuts)  # Abort before writing if legacy ranges are invalid.
            self.write(job_id, 'transcript.json', transcript)
            self.write(job_id, 'suggestions.json', suggestions)
            self.write(job_id, 'edit.json', edit)
            info.update(schema=1, legacy=False, source=identity, state='ready', stage='ready',
                        media=decisions['metadata'], config={}, timings={}, migrated_at=storage.now(),
                        source_verification=verification)
            validate_source(identity)
            # Commit marker last: interrupted migration is safe to repeat.
            self.write(job_id, 'job.json', info)
            if self._sources_cache is not None and identity['path'] not in self._sources_cache:
                self._sources_cache.append(identity['path'])
            return info
