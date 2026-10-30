"""Only settings consumed by the local Minimal V1 runtime."""
import json
import os
from pathlib import Path

os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

BASE_DIR = Path(__file__).resolve().parent.parent


def _user_data_dir():
    if os.name == 'nt':
        return Path(os.getenv('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'VideoEditor'
    return Path(os.getenv('XDG_DATA_HOME') or Path.home() / '.local' / 'share') / 'videoeditor'


def _data_dir():
    # A checkout keeps everything next to the code; an installed copy (read-only program
    # folder, or the installer's launcher) writes to the user's own data folder instead.
    override = os.getenv('VIDEOEDITOR_DATA_DIR')
    if override:
        return Path(override)
    if os.getenv('VIDEOEDITOR_INSTALLED') or not os.access(BASE_DIR, os.W_OK):
        return _user_data_dir()
    return BASE_DIR


DATA_DIR = _data_dir().resolve()
INSTALLED = DATA_DIR != BASE_DIR
LOCAL_CONFIG_DIR = DATA_DIR / 'config'
USER_SETTINGS_PATH = LOCAL_CONFIG_DIR / 'studio.local.json'
PERSONA_PATH = DATA_DIR / 'PERSONA.md'


def user_settings():
    try:
        value = json.loads(USER_SETTINGS_PATH.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_user_settings(**changes):
    value = dict(user_settings(), **changes)
    LOCAL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    USER_SETTINGS_PATH.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    return value


def _source_dir():
    # Order: environment, the folder picked in the Studio, then a sensible default.
    chosen = os.getenv('SOURCE_VIDEOS_DIR') or user_settings().get('source_dir')
    if chosen:
        return Path(chosen)
    return Path.home() / 'Videos' if INSTALLED else BASE_DIR / 'videos'


SOURCE_VIDEOS_DIR = _source_dir().resolve()
JOBS_DIR = Path(os.getenv('JOBS_DIR', str(DATA_DIR / 'jobs'))).resolve()
SERVER_HOST = os.getenv('SERVER_HOST', '127.0.0.1')
SERVER_PORT = int(os.getenv('SERVER_PORT', '8765'))
ALLOWED_VIDEO_EXTENSIONS = ('.mp4', '.mov', '.mkv')

# Auto-create directories if they do not exist
SOURCE_VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)
