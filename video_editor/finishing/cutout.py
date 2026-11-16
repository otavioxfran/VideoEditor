"""Optional rembg person cutout with a cache beside the source image."""
from pathlib import Path


class CutoutUnavailable(RuntimeError):
    pass


def available():
    try:
        import rembg  # noqa: F401
    except ImportError:
        return False
    return True


def cache_path(image_path):
    path = Path(image_path)
    return path.with_name(path.stem + '.cutout.png')


def cutout(image_path, model='u2net_human_seg', force=False):
    source = Path(image_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    target = cache_path(source)
    if not force and target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
        return target
    if not available():
        raise CutoutUnavailable(
            'rembg não está instalado; instale requirements-rembg.txt para recortar '
            'pessoas ou use o modo contain.'
        )
    from rembg import new_session, remove

    result = remove(source.read_bytes(), session=new_session(model))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(result)
    return target
