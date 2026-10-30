import json
from datetime import datetime, timezone
from video_editor.core.atomic_io import safe_atomic_write, safe_atomic_write_json


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def write(path, value):
    # Reject NaN before any filesystem operation.
    json.dumps(value, allow_nan=False)
    return safe_atomic_write_json(path, value)


def write_text(path, text):
    return safe_atomic_write(path, text)
