"""
video_editor.core.job_lock
==========================
Per-job exclusive lock primitive.

Guarantees:
1. Scope: Per job_id exclusively (jobs/<job_id>/.job.lock). Does not serialize unrelated jobs.
2. Dual Safety:
   - In-process: Thread-safe via threading.Lock per job_id.
   - Cross-process: OS-level mandatory/advisory file lock (msvcrt on Windows, fcntl on POSIX).
3. Timeout: Configurable bounded timeout (default 10s). Controlled JobLockTimeoutError on failure.
4. Clean Release: Release guaranteed in finally block.
"""

import os
import sys
import time
import logging
import threading
from pathlib import Path
from typing import Dict, Union, Any, Generator
from contextlib import contextmanager

from video_editor.core.security import assert_safe_job_id, assert_safe_write_path

logger = logging.getLogger(__name__)

# In-process per-job locks with ref-counting to eliminate unbounded memory growth
_THREAD_LOCKS: Dict[str, threading.Lock] = {}
_LOCK_REFS: Dict[str, int] = {}
_REGISTRY_LOCK = threading.Lock()


def _acquire_thread_lock(job_id: str) -> threading.Lock:
    with _REGISTRY_LOCK:
        if job_id not in _THREAD_LOCKS:
            _THREAD_LOCKS[job_id] = threading.Lock()
            _LOCK_REFS[job_id] = 0
        _LOCK_REFS[job_id] += 1
        return _THREAD_LOCKS[job_id]


def _release_thread_lock(job_id: str, lock: threading.Lock, was_acquired: bool = True) -> None:
    if was_acquired:
        try:
            lock.release()
        except RuntimeError:
            pass
    with _REGISTRY_LOCK:
        if job_id in _LOCK_REFS:
            _LOCK_REFS[job_id] -= 1
            if _LOCK_REFS[job_id] <= 0:
                _LOCK_REFS.pop(job_id, None)
                _THREAD_LOCKS.pop(job_id, None)


class JobLockTimeoutError(Exception):
    """Raised when an exclusive job lock cannot be acquired within the timeout period."""
    pass


@contextmanager
def job_lock(
    job_target: Any,
    timeout: float = 10.0,
    poll_interval: float = 0.05
) -> Generator[Path, None, None]:
    """
    Acquires an exclusive lock on jobs/<job_id>/.job.lock.
    
    Usage:
        with job_lock(job_ctx, timeout=10.0):
            # Critical section for job_ctx.job_id
            ...
    """
    if hasattr(job_target, "job_id") and hasattr(job_target, "job_dir"):
        job_id = str(job_target.job_id)
        job_dir = Path(job_target.job_dir).resolve()
    elif isinstance(job_target, (str, Path)):
        p = Path(job_target).resolve()
        if p.is_dir() and (p / "decisions.json").exists():
            job_id = p.name
            job_dir = p
        else:
            job_id = str(job_target)
            assert_safe_job_id(job_id)
            from video_editor.core.job_manager import JobManager
            job_ctx = JobManager().get_job_context(job_id)
            job_dir = job_ctx.job_dir.resolve()
    else:
        raise ValueError(f"Invalid job target for job_lock: {job_target}")

    assert_safe_job_id(job_id)
    lock_file = job_dir / ".job.lock"
    assert_safe_write_path(lock_file, "arquivo de lock do job")

    thread_lock = _acquire_thread_lock(job_id)

    # 1. Acquire thread lock
    start_time = time.time()
    acquired_thread = thread_lock.acquire(timeout=timeout)
    if not acquired_thread:
        _release_thread_lock(job_id, thread_lock, was_acquired=False)
        raise JobLockTimeoutError(
            f"Timeout ({timeout}s) waiting for thread lock on job '{job_id}'"
        )

    file_handle = None
    try:
        # 2. Acquire OS file lock
        elapsed = time.time() - start_time
        remaining_timeout = max(0.01, timeout - elapsed)
        deadline = time.time() + remaining_timeout

        lock_file.parent.mkdir(parents=True, exist_ok=True)
        file_handle = open(lock_file, "a+b")

        os_locked = False
        while time.time() < deadline:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    file_handle.seek(0)
                    msvcrt.locking(file_handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                os_locked = True
                break
            except (OSError, IOError, PermissionError):
                time.sleep(poll_interval)

        if not os_locked:
            raise JobLockTimeoutError(
                f"Timeout ({timeout}s) waiting for OS file lock on job '{job_id}' (.job.lock)"
            )

        yield lock_file

    finally:
        # Guaranteed unlock
        if file_handle is not None:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    file_handle.seek(0)
                    try:
                        msvcrt.locking(file_handle.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                else:
                    import fcntl
                    try:
                        fcntl.flock(file_handle.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
            except Exception as e:
                logger.warning(f"Error unlocking OS lock for job {job_id}: {e}")
            finally:
                try:
                    file_handle.close()
                except Exception:
                    pass

        _release_thread_lock(job_id, thread_lock, was_acquired=True)
