"""Read-only sources and allowlisted destinations, including symlink/hardlink aliases."""
import os
from pathlib import Path
from video_editor.core.security import assert_safe_write_path, assert_safe_job_id


def source_identity(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ValueError("Fonte precisa ser um arquivo.")
    stat = path.stat()
    return dict(path=str(path), size=stat.st_size, mtime_ns=stat.st_mtime_ns,
                device=stat.st_dev, inode=stat.st_ino)


def validate_source(identity):
    current = source_identity(identity['path'])
    if current != identity:
        raise ValueError("Fonte alterada. Crie outro job; decisões não serão reaplicadas.")
    return Path(current['path'])


def destination(path, root, sources=()):
    path, root = Path(path).resolve(), Path(root).resolve()
    if path == root or not path.is_relative_to(root):
        raise PermissionError("Destino fora do workspace do job.")
    assert_safe_write_path(path)
    for source in sources:
        source = Path(source).resolve()
        if path == source or (path.exists() and source.exists() and os.path.samefile(path, source)):
            raise PermissionError("Destino equivale a um raw protegido.")
    return path


def job_directory(root, job_id):
    assert_safe_job_id(job_id, root)
    path = (Path(root) / job_id).resolve()
    if path.parent != Path(root).resolve() or not path.is_dir():
        raise FileNotFoundError("Job não encontrado.")
    return path
