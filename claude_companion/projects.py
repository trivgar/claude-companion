"""The one list of project folders the dashboard may act in.

``[projects]`` in config.toml names each folder, as ``name = "path"`` or a
list of ``{ name, path }`` rows. The Files tab and the New session launcher
both read it. ``[new_session].projects``, where the launcher first kept its
own list, is still read so an existing config keeps working; a name in both
takes the ``[projects]`` path.
"""
from __future__ import annotations

from . import config


def pairs(raw: object) -> list[tuple[str, str]]:
    """``(name, path)`` from a table or a list of rows; malformed rows are skipped."""
    if isinstance(raw, dict):
        items = list(raw.items())
    elif isinstance(raw, (list, tuple)):
        items = [(row.get("name"), row.get("path")) for row in raw if isinstance(row, dict)]
    else:
        items = []
    out = []
    for name, path in items:
        if isinstance(name, str) and isinstance(path, str) and name.strip() and path.strip():
            out.append((" ".join(name.split()), path))
    return out


def configured() -> list[tuple[str, str]]:
    """Every configured project, ``[projects]`` first, each name once."""
    legacy = config.get("new_session", {})
    rows = pairs(config.get("projects", {}))
    rows += pairs(legacy.get("projects") if isinstance(legacy, dict) else None)
    seen, out = set(), []
    for name, path in rows:
        if name.casefold() not in seen:
            seen.add(name.casefold())
            out.append((name, path))
    return out
