"""
video_editor.core.atomic_io
===========================
Atomic, crash-safe, write-guarded I/O operations.

Guarantees:
1. Security: Every write path passes assert_safe_write_path (never writes to the source folder).
2. Atomicity: Writes first to a hidden temporary file in the same directory, flushes, fsyncs,
   closes, and renames with os.replace.
3. Resilience: If an error or power loss occurs mid-write, the target file is never truncated
   or corrupted.
"""

import os
import json
import uuid
from pathlib import Path
from typing import Dict, Any, Union

from video_editor.core.security import assert_safe_write_path


def safe_atomic_write(
    target_path: Union[Path, str],
    content: Union[str, bytes],
    encoding: str = "utf-8",
    description: str = "escrita atômica segura"
) -> Path:
    """
    Safely and atomically writes content to target_path using flush + fsync + os.replace.
    Protects target from truncation if writing fails midway.
    """
    target = Path(target_path).resolve()
    assert_safe_write_path(target, description)

    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.parent / f".{target.name}.tmp.{uuid.uuid4().hex[:8]}"

    try:
        if isinstance(content, bytes):
            with open(temp_path, "wb") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
        else:
            with open(temp_path, "w", encoding=encoding) as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())

        # Atomic replacement on Windows and POSIX
        os.replace(temp_path, target)
        return target
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def safe_atomic_write_json(
    target_path: Union[Path, str],
    data: Dict[str, Any],
    indent: int = 2,
    description: str = "escrita atômica de JSON"
) -> Path:
    """Serializes data as formatted JSON and writes atomically."""
    content = json.dumps(data, indent=indent, ensure_ascii=False)
    return safe_atomic_write(target_path, content, encoding="utf-8", description=description)


def atomic_read_json(path: Union[Path, str]) -> Dict[str, Any]:
    """Reads and parses a JSON file with utf-8 encoding."""
    target = Path(path).resolve()
    if not target.exists():
        raise FileNotFoundError(f"Arquivo JSON não encontrado: {target}")
    with open(target, "r", encoding="utf-8") as f:
        return json.load(f)
