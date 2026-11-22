"""Read-only sources and allowlisted destinations, including symlink/hardlink aliases."""
from pathlib import Path

from video_editor.core.security import assert_safe_write_path, assert_safe_job_id


_PROTECTED_CACHE = {}


def clear_source_cache():
    _PROTECTED_CACHE.clear()


def _protected_identity(source):
    key = str(source)
    cached = _PROTECTED_CACHE.get(key)
    if cached is not None:
        return cached
    resolved = Path(source).resolve()
    if resolved.is_file():
        stat = resolved.stat()
        value = (str(resolved), stat.st_dev, stat.st_ino)
    else:
        value = (str(resolved), None, None)
    _PROTECTED_CACHE[key] = value
    return value


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
    path_stat = path.stat() if path.exists() else None
    for source in {str(item) for item in sources}:
        resolved, device, inode = _protected_identity(source)
        if str(path) == resolved or (
            path_stat is not None
            and device is not None
            and path_stat.st_dev == device
            and path_stat.st_ino == inode
        ):
            raise PermissionError("Destino equivale a um raw protegido.")
    return path


def job_directory(root, job_id):
    assert_safe_job_id(job_id, root)
    path = (Path(root) / job_id).resolve()
    if path.parent != Path(root).resolve() or not path.is_dir():
        raise FileNotFoundError("Job não encontrado.")
    return path
