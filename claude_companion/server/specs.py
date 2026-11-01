"""Read-only specs and plans from the configured project roots.

Documents are discovered only from fixed defaults and ``spec_globs`` in the
configuration.  Both discovery and reading go through ``safe_paths`` so a
document name from a browser cannot escape its configured project.
"""
from __future__ import annotations

import os
import re
import time
from datetime import date, datetime
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, HTTPException

from .. import config, projects, safe_paths
from . import auth

TITLE = "Specs"
router = APIRouter()

MAX_BYTES = 1024 * 1024
DEFAULT_GLOBS = (("docs/superpowers/specs/*.md", "spec"),
                 ("docs/superpowers/plans/*.md", "plan"))
_HEADING = re.compile(r"^\s{0,3}#{1,6}[ \t]+(.+?)\s*#*\s*$")
_DATED_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _relative(value: str) -> str | None:
    """A non-empty project-relative POSIX path, without traversal."""
    path = PurePosixPath(str(value or ""))
    if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        return None
    return path.as_posix()


def _glob_values() -> tuple[str, ...]:
    """Configured extra globs, with an environment value split like other lists."""
    value = config.get("spec_globs") or ()
    if isinstance(value, str):
        value = value.split(os.pathsep)
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(path for path in value if isinstance(path, str) and _relative(path) is not None)


def _kind(relative: str) -> str:
    """Plans are named by a plan-containing file or folder; other extras are specs."""
    return "plan" if any("plan" in part.casefold() for part in PurePosixPath(relative).parts) else "spec"


def _project_roots() -> list[tuple[str, Path]]:
    """Existing shared ``[projects]`` roots, without accepting caller paths."""
    out = []
    for name, value in projects.configured():
        try:
            root = Path(value).expanduser().resolve(strict=True)
        except OSError:
            continue
        if root.is_dir():
            out.append((name, root))
    return out


def _snapshot(path: Path) -> tuple[str, os.stat_result] | None:
    """A stable, bounded text snapshot, or None for an unreadable/large file."""
    try:
        before = path.stat()
        if before.st_size > MAX_BYTES:
            return None
        with path.open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
        after = path.stat()
    except OSError:
        return None
    if len(raw) > MAX_BYTES or after.st_size > MAX_BYTES:
        return None
    if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
        return None
    return raw.decode("utf-8", "replace"), after


def _title(text: str, filename: str) -> str:
    """The first Markdown heading, or the filename when no heading exists."""
    for line in text.splitlines():
        matched = _HEADING.match(line)
        if matched:
            heading = matched.group(1).strip()
            if heading:
                return heading
    return filename


def _document_date(filename: str, modified: float) -> tuple[str, float]:
    """The leading filename date, otherwise the modification date, for display and sort."""
    matched = _DATED_NAME.match(filename)
    if matched:
        try:
            stamped = date.fromisoformat(matched.group(1))
        except ValueError:
            pass
        else:
            return stamped.isoformat(), datetime.combine(stamped, datetime.min.time()).timestamp()
    return time.strftime("%Y-%m-%d", time.localtime(modified)), modified


def _matches(root: Path, pattern: str):
    """Regular, non-symlinked matches below one project root."""
    try:
        matches = root.glob(pattern)
        for candidate in matches:
            try:
                relative = candidate.relative_to(root).as_posix()
            except ValueError:
                continue
            checked = safe_paths.file(root, relative)
            if checked is not None:
                yield relative, checked
    except (OSError, ValueError):
        return


def _documents_for(name: str, root: Path) -> list[dict]:
    """Documents under one configured project, with duplicate glob matches removed."""
    patterns = list(DEFAULT_GLOBS) + [(pattern, None) for pattern in _glob_values()]
    found: set[str] = set()
    out = []
    for pattern, default_kind in patterns:
        for relative, path in _matches(root, pattern):
            if relative in found:
                continue
            found.add(relative)
            snap = _snapshot(path)
            if snap is None:
                continue
            text, stat = snap
            shown_date, sort_date = _document_date(path.name, stat.st_mtime)
            out.append({"project": name, "path": relative, "title": _title(text, path.name),
                        "kind": default_kind or _kind(relative),
                        "date": shown_date, "modified": stat.st_mtime, "_sort_date": sort_date})
    return out


def listing() -> list[dict]:
    """Every configured project's discoverable document, newest first."""
    rows = [doc for name, root in _project_roots() for doc in _documents_for(name, root)]
    rows.sort(key=lambda doc: (doc["_sort_date"], doc["modified"], doc["project"].casefold(), doc["path"]), reverse=True)
    for doc in rows:
        doc.pop("_sort_date", None)
    return rows


def read(project: str, relative: str) -> dict:
    """Read exactly one document that is currently in the configured listing."""
    relative = _relative(relative)
    if relative is None:
        return {"ok": False, "detail": "No such document."}
    for name, root in _project_roots():
        if name != project:
            continue
        matches = {doc["path"]: doc for doc in _documents_for(name, root)}
        doc = matches.get(relative)
        path = safe_paths.file(root, relative)
        if doc is None or path is None:
            return {"ok": False, "detail": "No such document."}
        snap = _snapshot(path)
        if snap is None:
            return {"ok": False, "detail": "That document cannot be read here."}
        text, _ = snap
        return {"ok": True, **doc, "text": text, "bytes": len(text.encode("utf-8"))}
    return {"ok": False, "detail": "No such document."}


@router.get("/api/specs", dependencies=[Depends(auth.require_token)])
def get_specs():
    return {"docs": listing()}


@router.get("/api/specs/file", dependencies=[Depends(auth.require_token)])
def get_spec(project: str, path: str):
    result = read(project, path)
    if not result["ok"]:
        raise HTTPException(404, result["detail"])
    return result
