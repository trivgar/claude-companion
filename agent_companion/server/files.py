"""Configured-project file browser and guarded dashboard edits.

Only files that git would show as tracked or unignored are visible.  Every
lookup goes back through ``safe_paths`` so a request cannot use a configured
project as a way to read or write elsewhere on disk.
"""
from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import config, safe_paths
from .. import projects as configured_projects
from . import auth

TITLE = "Files"
router = APIRouter()

MAX_BYTES = 1024 * 1024
GIT_TIMEOUT_S = 20
SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "env", ".env", ".virtualenv"}


@dataclass(frozen=True)
class Snapshot:
    raw: bytes
    text: str
    mtime_ns: int
    sha256: str


def projects() -> list[dict[str, str]]:
    """Configured, existing project roots, without exposing arbitrary paths.

    The list is the shared ``[projects]`` table (agent_companion.projects).
    """
    items = configured_projects.configured()
    out = []
    for name, value in items:
        name = str(name or "").strip()
        if not name or not isinstance(value, str) or not value.strip():
            continue
        try:
            root = Path(value).expanduser().resolve(strict=True)
        except OSError:
            continue
        if root.is_dir():
            out.append({"name": name, "path": str(root)})
    return sorted(out, key=lambda item: item["name"].lower())


def project(name: str) -> Path | None:
    """The root for a configured project name, and never a caller path."""
    for item in projects():
        if item["name"] == name:
            return Path(item["path"])
    return None


def _git(root: Path, *args: str, timeout: float = GIT_TIMEOUT_S) -> subprocess.CompletedProcess | None:
    """Run git without index refresh locks or an interactive input stream."""
    try:
        return subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", str(root), *args],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _skipped(relative: str) -> bool:
    return any(part in SKIP_DIRS for part in PurePosixPath(relative).parts)


def _snapshot(path: Path) -> Snapshot | None:
    """A bounded UTF-8 regular file snapshot, or None for non-editor files."""
    try:
        before = path.stat()
        if before.st_size > MAX_BYTES:
            return None
        raw = path.read_bytes()
        after = path.stat()
    except OSError:
        return None
    if len(raw) > MAX_BYTES or b"\0" in raw:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    # Do not advertise a snapshot assembled while another writer changed it.
    if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
        return None
    return Snapshot(raw, text, after.st_mtime_ns, hashlib.sha256(raw).hexdigest())


def _listable(path: Path) -> bool:
    """A cheap listing check: size and the first 4 KB, never the whole file.
    Opening a file still takes a full snapshot."""
    try:
        if path.stat().st_size > MAX_BYTES:
            return False
        with path.open("rb") as handle:
            head = handle.read(4096)
    except OSError:
        return False
    if b"\0" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        return exc.start >= len(head) - 3      # a character cut at the 4 KB edge
    return True


def visible_files(root: Path) -> list[str]:
    """Text files under the project that git does not ignore.

    ``ls-files --others --exclude-standard`` includes untracked files but not
    ignored ones.  A tracked file below a generated directory is still omitted:
    those directories are intentionally never part of this browser.
    """
    result = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if result is None or result.returncode:
        return []
    out = []
    for raw_name in result.stdout.split(b"\0"):
        if not raw_name:
            continue
        try:
            relative = raw_name.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if _skipped(relative):
            continue
        path = safe_paths.file(root, relative)
        if path is not None and _listable(path):
            out.append(relative)
    return sorted(out, key=str.lower)


def _relative(value: str, allow_empty: bool = False) -> str | None:
    """A simple project-relative POSIX path, with no traversal components."""
    value = str(value or "")
    if not value:
        return "" if allow_empty else None
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        return None
    return path.as_posix()


def tree(name: str, folder: str = "") -> dict:
    root = project(name)
    relative = _relative(folder, allow_empty=True)
    if root is None:
        return {"ok": False, "detail": "No such configured project."}
    if relative is None:
        return {"ok": False, "detail": "That folder is outside the project."}
    files = visible_files(root)
    prefix = relative + "/" if relative else ""
    dirs, here = set(), []
    for item in files:
        if not item.startswith(prefix):
            continue
        tail = item[len(prefix):]
        if "/" in tail:
            dirs.add(tail.split("/", 1)[0])
        else:
            here.append(tail)
    if relative and not dirs and not here:
        return {"ok": False, "detail": "No such folder."}
    return {"ok": True, "project": name, "path": relative,
            "dirs": sorted(dirs, key=str.lower), "files": sorted(here, key=str.lower)}


def open_file(name: str, value: str) -> dict:
    root = project(name)
    relative = _relative(value)
    if root is None:
        return {"ok": False, "detail": "No such configured project."}
    if relative is None or relative not in visible_files(root):
        return {"ok": False, "detail": "No such text file."}
    path = safe_paths.file(root, relative)
    snap = _snapshot(path) if path is not None else None
    if snap is None:
        return {"ok": False, "detail": "That file cannot be opened here."}
    return {"ok": True, "project": name, "path": relative, "text": snap.text,
            "mtime_ns": snap.mtime_ns, "sha256": snap.sha256, "bytes": len(snap.raw)}


def _status(root: Path, relative: str) -> str | None:
    result = _git(root, "status", "--porcelain=v1", "--untracked-files=all", "--", relative)
    if result is None or result.returncode:
        return None
    return "clean" if not result.stdout else "dirty"


def _replace(root: Path, relative: str, path: Path, data: bytes) -> str | None:
    """Atomically replace one verified project file, returning an error if any."""
    temporary = None
    try:
        # Recheck immediately before creating a sibling.  os.replace replaces
        # a symlink itself rather than following it, and safe_paths rejects one
        # in the parent chain.
        if safe_paths.file(root, relative) != path:
            return "The file is no longer inside the project."
        mode = stat.S_IMODE(path.stat().st_mode)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        return None
    except OSError as exc:
        return f"Could not save the file: {exc.strerror or exc}"
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def save(name: str, value: str, text: str, mtime_ns: int, sha256: str) -> dict:
    """Save an unchanged-on-disk edit, committing only a previously clean file."""
    root = project(name)
    relative = _relative(value)
    if root is None:
        return {"ok": False, "status": 404, "detail": "No such configured project."}
    if relative is None or relative not in visible_files(root):
        return {"ok": False, "status": 404, "detail": "No such text file."}
    path = safe_paths.file(root, relative)
    current = _snapshot(path) if path is not None else None
    if current is None:
        return {"ok": False, "status": 409, "detail": "That file can no longer be edited here."}
    if current.mtime_ns != mtime_ns or current.sha256 != sha256:
        return {"ok": False, "status": 409, "detail": "The file changed on disk since it was opened."}
    status = _status(root, relative)
    # Status may spend long enough on a large checkout for another writer to
    # arrive.  Test the version the browser opened one final time.
    current = _snapshot(path)
    if current is None or current.mtime_ns != mtime_ns or current.sha256 != sha256:
        return {"ok": False, "status": 409, "detail": "The file changed on disk since it was opened."}
    if "\r\n" in current.text:
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    data = text.encode("utf-8")
    if data == current.raw:
        return {"ok": True, "unchanged": True, "committed": False}
    error = _replace(root, relative, path, data)
    if error:
        return {"ok": False, "status": 500, "detail": error}
    out = {"ok": True, "committed": False}
    if status == "clean":
        result = _git(root, "commit", "--only", "-m", f"Edit {relative} from the dashboard", "--", relative,
                      timeout=60)
        if result is not None and result.returncode == 0:
            out["committed"] = True
        else:
            message = (result.stderr if result is not None else b"")[-400:].decode("utf-8", "replace").strip()
            out["detail"] = "Saved, but the commit failed" + (f": {message}" if message else ".")
    elif status == "dirty":
        out["detail"] = "Saved, not committed because the file had uncommitted changes before the save."
    else:
        out["detail"] = "Saved, not committed because git status could not be checked."
    return out


class Save(BaseModel):
    project: str
    path: str
    text: str
    mtime_ns: int
    sha256: str


@router.get("/api/files", dependencies=[Depends(auth.require_token)])
def get_projects():
    return {"projects": [{"name": item["name"]} for item in projects()]}


@router.get("/api/files/tree", dependencies=[Depends(auth.require_token)])
def get_tree(project: str, path: str = ""):
    result = tree(project, path)
    if not result["ok"]:
        raise HTTPException(404, result["detail"])
    return result


@router.get("/api/files/file", dependencies=[Depends(auth.require_token)])
def get_file(project: str, path: str):
    result = open_file(project, path)
    if not result["ok"]:
        raise HTTPException(404, result["detail"])
    return result


@router.post("/api/files/save", dependencies=[Depends(auth.require_write)])
def save_file(body: Save):
    result = save(body.project, body.path, body.text, body.mtime_ns, body.sha256)
    if not result["ok"]:
        raise HTTPException(int(result.get("status", 400)), result["detail"])
    return result
