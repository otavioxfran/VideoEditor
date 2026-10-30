"""First-run checks: is this computer ready to cut? FFmpeg, GPU, speech model, disk."""
import shutil
import subprocess
import threading
from functools import lru_cache
from pathlib import Path

from video_editor.settings import DATA_DIR

MODEL_FILES = ('config.json', 'preprocessor_config.json', 'model.bin', 'tokenizer.json', 'vocabulary.')
# Without an NVIDIA card the big model is too slow for a 20-minute video; small is the default there.
GPU_MODEL, CPU_MODEL = 'large-v3-turbo', 'small'


def _quiet(command, timeout=5):
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.decode('utf-8', 'replace') if result.returncode == 0 else None


def ffmpeg():
    path = shutil.which('ffmpeg')
    probe = shutil.which('ffprobe')
    version = None
    if path:
        output = _quiet([path, '-hide_banner', '-version'])
        version = output.split('\n', 1)[0].replace('ffmpeg version ', '').split(' ')[0] if output else None
    return dict(ok=bool(path and probe), path=path, version=version)


@lru_cache(maxsize=1)
def gpu():
    try:
        import ctranslate2
        count = ctranslate2.get_cuda_device_count()
    except Exception:
        count = 0
    name = None
    if count and shutil.which('nvidia-smi'):
        output = _quiet(['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader'])
        name = output.strip().split('\n')[0] if output else None
    return dict(ok=count > 0, count=count, name=name)


def recommended_model():
    return GPU_MODEL if gpu()['ok'] else CPU_MODEL


def _repo(model):
    from faster_whisper.utils import _MODELS
    return _MODELS.get(model, model)


def _cache_dir(model):
    from huggingface_hub import constants
    return Path(constants.HF_HUB_CACHE) / f"models--{_repo(model).replace('/', '--')}"


_ready = set()


def model_ready(model):
    if model in _ready:
        return True
    try:
        from faster_whisper.utils import download_model
        download_model(model, local_files_only=True)
        _ready.add(model)
        return True
    except Exception:
        return False


class ModelDownloads:
    """One background download at a time, with progress read from the Hugging Face cache."""

    def __init__(self):
        self._lock = threading.Lock()
        self.state = {}

    def _total(self, model):
        from huggingface_hub import HfApi
        info = HfApi().model_info(_repo(model), files_metadata=True)
        return sum(item.size or 0 for item in info.siblings
                   if any(item.rfilename.startswith(name) for name in MODEL_FILES))

    def _downloaded(self, model):
        blobs = _cache_dir(model) / 'blobs'
        try:
            return sum(path.stat().st_size for path in blobs.iterdir() if path.is_file())
        except OSError:
            return 0

    def status(self, model):
        with self._lock:
            state = dict(self.state.get(model) or {})
        if state.get('state') == 'downloading':
            total = state.get('total') or 0
            done = self._downloaded(model)
            state.update(downloaded=done, progress=min(0.99, done / total) if total else None)
        return state

    def start(self, model):
        with self._lock:
            if (self.state.get(model) or {}).get('state') == 'downloading':
                return False
            self.state[model] = dict(state='downloading', total=None, error=None)
        threading.Thread(target=self._run, args=(model,), daemon=True, name=f'model-{model}').start()
        return True

    def _run(self, model):
        try:
            try:
                total = self._total(model)
            except Exception:
                total = None
            with self._lock:
                self.state[model]['total'] = total
            from faster_whisper.utils import download_model
            download_model(model)
            with self._lock:
                self.state[model] = dict(state='done', progress=1.0, error=None)
        except Exception as error:
            with self._lock:
                self.state[model] = dict(state='failed', error=str(error)[:300])


downloads = ModelDownloads()


def status(model=None):
    model = model or recommended_model()
    ready = model_ready(model)
    try:
        free = shutil.disk_usage(DATA_DIR).free
    except OSError:
        free = None
    tools = ffmpeg()
    return dict(
        ffmpeg=tools,
        gpu=gpu(),
        model=dict(name=model, recommended=recommended_model(), ready=ready, download=downloads.status(model)),
        disk=dict(free_bytes=free, path=str(DATA_DIR)),
        ready=bool(tools['ok'] and ready),
    )
