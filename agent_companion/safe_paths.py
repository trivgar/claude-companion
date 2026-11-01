"""Files in optional external directories, kept inside their roots."""
from __future__ import annotations

from pathlib import Path


def _resolve(root: Path, relative: str | Path) -> Path | None:
    """Resolve a real, non-symlinked path below root, or return None."""
    root, relative = Path(root), Path(relative)
    if relative.is_absolute() or not relative.parts or any(part in ("", ".", "..") for part in relative.parts):
        return None
    try:
        resolved_root = root.resolve(strict=True)
        candidate = root
        for part in relative.parts:
            candidate /= part
            if candidate.is_symlink():
                return None
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    return resolved if resolved.is_relative_to(resolved_root) else None


def file(root: Path, relative: str | Path) -> Path | None:
    """A regular file below root, with no symlink below that root."""
    candidate = _resolve(root, relative)
    return candidate if candidate is not None and candidate.is_file() else None


def directory(root: Path, relative: str | Path) -> Path | None:
    """A directory below root, with no symlink below that root."""
    candidate = _resolve(root, relative)
    return candidate if candidate is not None and candidate.is_dir() else None
