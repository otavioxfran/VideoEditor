"""Only settings consumed by the local Minimal V1 runtime."""
import os
from pathlib import Path

os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

BASE_DIR = Path(__file__).resolve().parent.parent
SOURCE_VIDEOS_DIR = Path(os.getenv('SOURCE_VIDEOS_DIR', str(BASE_DIR / 'videos'))).resolve()
JOBS_DIR = Path(os.getenv('JOBS_DIR', str(BASE_DIR / 'jobs'))).resolve()
SERVER_HOST = os.getenv('SERVER_HOST', '127.0.0.1')
SERVER_PORT = int(os.getenv('SERVER_PORT', '8765'))
ALLOWED_VIDEO_EXTENSIONS = ('.mp4', '.mov', '.mkv')

# Auto-create directories if they do not exist
SOURCE_VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)


