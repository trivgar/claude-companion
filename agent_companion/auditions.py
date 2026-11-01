"""Read and decide trial voice clips in a configured speech directory."""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path

from . import safe_paths

NAME = re.compile(r"[A-Za-z0-9_-]{1,32}")
CLIP = re.compile(r"[A-Za-z0-9 _.-]{1,80}\.(wav|mp3)")
TYPES = {"wav": "audio/wav", "mp3": "audio/mpeg"}


def root(tts: Path) -> Path:
    return Path(tts) / "auditions"


def _decision(directory: Path) -> dict | None:
    try:
        value = json.loads((directory / "decision.json").read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and "tier" in value else None


def _clip_ok(name: str) -> bool:
    return bool(CLIP.fullmatch(name)) and ".." not in name


def listing(tts: Path) -> list[dict]:
    base = root(tts)
    try:
        directories = sorted(path for path in base.iterdir() if NAME.fullmatch(path.name))
    except OSError:
        return []
    out = []
    for path in directories:
        directory = safe_paths.directory(base, path.name)
        if directory is None:
            continue
        try:
            clips = sorted(path.name for path in directory.iterdir() if path.is_file() and _clip_ok(path.name))
        except OSError:
            clips = []
        try:
            note = (directory / "note.txt").read_text(errors="replace").strip().splitlines()[0][:500]
        except (OSError, IndexError):
            note = ""
        out.append({"name": directory.name, "note": note, "clips": clips, "decision": _decision(directory)})
    return out


def clip_path(tts: Path, name: str, clip: str) -> Path | None:
    if not NAME.fullmatch(name) or not _clip_ok(clip):
        return None
    return safe_paths.file(root(tts), Path(name) / clip)


def exists(tts: Path, name: str) -> bool:
    return bool(NAME.fullmatch(name)) and safe_paths.directory(root(tts), name) is not None


def valid_tier(tier) -> bool:
    return tier == "no" or (type(tier) is int and tier in (1, 2, 3))


def _atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def set_tier(tts: Path, name: str, tier: int) -> None:
    conf = Path(tts) / "tiers.conf"
    try:
        lines = conf.read_text().splitlines()
    except OSError:
        lines = []
    key, replacement, written = name.lower(), f"{name.lower()}={tier}", False
    kept = []
    for line in lines:
        if "=" in line and line.split("=", 1)[0].strip().lower() == key:
            if not written:
                kept.append(replacement)
                written = True
        else:
            kept.append(line)
    _atomic(conf, "\n".join(kept + ([] if written else [replacement])) + "\n")


def decide(tts: Path, name: str, tier) -> None:
    directory = safe_paths.directory(root(tts), name)
    if directory is None:
        raise ValueError("no such audition")
    if tier != "no":
        set_tier(tts, name, tier)
    _atomic(directory / "decision.json", json.dumps({"tier": tier, "at": time.time()}))
