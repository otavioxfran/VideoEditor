import re
from pathlib import Path
from typing import Union, Optional
from video_editor.settings import SOURCE_VIDEOS_DIR, JOBS_DIR

class SourceDirectoryProtectionError(PermissionError):
    """Raised when an operation attempts to write, delete, move, or overwrite inside protected source directories."""
    pass

class InvalidJobIdError(ValueError):
    """Raised when a job_id contains illegal characters, traversal sequences, or resolves outside JOBS_DIR."""
    pass

class UnsafePathError(PermissionError):
    """Raised when a path attempts directory traversal or touches unauthorized system areas."""
    pass

def assert_safe_job_id(job_id: str, jobs_dir: Optional[Union[str, Path]] = None) -> str:
    """
    Strictly validates that a job_id is safe:
    - Non-empty string
    - Contains only alphanumeric characters, underscores, dashes, and periods
    - Cannot be '.' or '..'
    - Must not contain path separators ('/', '\\'), colons, null bytes, or wildcards
    - Resolved path must remain strictly a direct child of JOBS_DIR
    """
    if not isinstance(job_id, str) or not job_id.strip():
        raise InvalidJobIdError("[SEGURANÇA BLOQUEADA] job_id não pode ser vazio ou nulo.")

    clean_id = job_id.strip()
    if clean_id in (".", ".."):
        raise InvalidJobIdError(f"[SEGURANÇA BLOQUEADA] job_id inválido: '{clean_id}'")

    if "\x00" in clean_id or "/" in clean_id or "\\" in clean_id or ":" in clean_id:
        raise InvalidJobIdError(f"[SEGURANÇA BLOQUEADA] job_id contém caracteres proibidos de separação ou controle: '{clean_id}'")

    if not re.match(r"^[a-zA-Z0-9_\-\.]+$", clean_id):
        raise InvalidJobIdError(f"[SEGURANÇA BLOQUEADA] job_id contém caracteres inválidos: '{clean_id}'")

    base_jobs_dir = Path(jobs_dir or JOBS_DIR).resolve()
    resolved_job_dir = (base_jobs_dir / clean_id).resolve()

    if resolved_job_dir.parent != base_jobs_dir:
        raise InvalidJobIdError(
            f"[SEGURANÇA BLOQUEADA] Path traversal detectado no job_id '{clean_id}'. "
            f"O path resolvido '{resolved_job_dir}' não é filho direto de '{base_jobs_dir}'."
        )

    return clean_id

def _normalize_path(path: Union[str, Path]) -> Path:
    return Path(path).resolve()


def is_protected_source_path(path: Union[str, Path]) -> bool:
    """
    Checks if a given path is located inside or is the protected SOURCE_VIDEOS_DIR.
    Performs case-insensitive check on Windows.
    """
    target = _normalize_path(path)
    source_dir = _normalize_path(SOURCE_VIDEOS_DIR)

    # Convert to lowercase strings for case-insensitive Windows comparison
    target_str = str(target).lower().replace("/", "\\")
    source_str = str(source_dir).lower().replace("/", "\\")

    if target_str == source_str:
        return True

    # Check if target starts with source_dir + backslash
    if not source_str.endswith("\\"):
        source_str += "\\"

    return target_str.startswith(source_str)

def assert_safe_write_path(path: Union[str, Path], operation: str = "escrita") -> Path:
    """
    Validates that a path is NOT located within the protected source directory.
    Raises SourceDirectoryProtectionError if violated.
    """
    target = _normalize_path(path)
    if is_protected_source_path(target):
        err_msg = (
            f"[SEGURANÇA BLOQUEADA] Tentativa de {operation} na pasta protegida de brutos: '{target}'. "
            f"A pasta '{SOURCE_VIDEOS_DIR}' é estritamente READ-ONLY (apenas LISTAR, LER e COPIAR são permitidos)."
        )
        raise SourceDirectoryProtectionError(err_msg)
    return target

def assert_safe_ffmpeg_output(input_path: Union[str, Path], output_path: Union[str, Path]) -> Path:
    """
    Guarantees that FFmpeg output is never identical to input and never targets the source directory.
    """
    in_norm = _normalize_path(input_path)
    out_norm = _normalize_path(output_path)

    if in_norm == out_norm:
        raise ValueError(
            f"[SEGURANÇA BLOQUEADA] O arquivo de saída do FFmpeg não pode ser idêntico ao de entrada: '{out_norm}'"
        )

    return assert_safe_write_path(out_norm, "saída do FFmpeg")

def safe_open(path: Union[str, Path], mode: str = "r", *args, **kwargs):
    """
    Safely opens a file, guaranteeing that protected source directories
    cannot be opened in any writing, appending, creating, or modifying modes.
    """
    write_modes = {"w", "a", "x", "+"}
    if any(char in mode for char in write_modes):
        assert_safe_write_path(path, f"abertura em modo de escrita ('{mode}')")
    return open(path, mode, *args, **kwargs)

def safe_delete(path: Union[str, Path]) -> None:
    """Safely deletes a file, asserting that it is NOT inside protected source directories."""
    target = assert_safe_write_path(path, "exclusão")
    Path(target).unlink()

def safe_rename(src: Union[str, Path], dst: Union[str, Path]) -> None:
    """Safely renames a file, asserting that neither source nor destination is inside protected source directories."""
    s = assert_safe_write_path(src, "renomeação (origem)")
    d = assert_safe_write_path(dst, "renomeação (destino)")
    Path(s).rename(d)

def safe_move(src: Union[str, Path], dst: Union[str, Path]) -> None:
    """Safely moves a file, asserting that neither source nor destination is inside protected source directories."""
    import shutil
    s = assert_safe_write_path(src, "movimentação (origem)")
    d = assert_safe_write_path(dst, "movimentação (destino)")
    shutil.move(s, d)
