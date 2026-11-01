"""Settings: what the app is running with, and the few things the page may change.

The page may change the saved context line, comms (hold, volume, a test clip)
and take a paired device off. It may NOT write config.toml: anything that
changes what the app reads stays in the file, by hand or by the owner's Claude.
"""
from __future__ import annotations

import os
import shutil
import re
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import config
from . import auth
from . import comms as comms_mod
from .features import FEATURES

TITLE = "Settings"
router = APIRouter()
COMMS_DIR = Path(__file__).resolve().parents[2] / "comms"
SCRIPT_TIMEOUT_S = 90
DOCS = ("INSTALL.md", "COLLAB.md")
COMMS_NOT_INSTALLED = "Comms is not installed yet: INSTALL.md step 10."


def tts_dir() -> Path:
    return Path(str(config.get("tts_dir") or os.environ.get("TTS_DIR") or COMMS_DIR))


_which = shutil.which   # tests stub this; a dev machine may have piper on PATH


def piper_installed() -> bool:
    """Whether speak.sh can use the Piper installed by comms/install.sh."""
    data = Path(os.environ.get("COMMS_DATA_DIR") or Path.home() / ".local" / "share" / "agent-companion")
    piper = Path(os.environ.get("PIPER_BIN") or data / "piper" / "piper" / "piper")
    if piper.is_file() and os.access(piper, os.X_OK):
        return True
    return not os.environ.get("PIPER_BIN") and _which("piper") is not None   # speak.sh's own fallback


def _script_env() -> dict:
    env = dict(os.environ)
    env["TTS_DIR"] = str(tts_dir())
    env["COMMS_STATE_DIR"] = str(comms_mod.state_dir())
    env["COMMS_CLIPS_DIR"] = str(comms_mod.clips_dir())
    return env


def _run(script: str, *args: str, speaker: str | None = None) -> subprocess.CompletedProcess:
    env = _script_env()
    if speaker:
        env["TTS_SPEAKER"] = speaker
    path = tts_dir() / script
    if not path.is_file():
        raise HTTPException(503, f"{script} is not in {tts_dir()}")
    try:
        return subprocess.run(["bash", str(path), *args], env=env, capture_output=True, text=True,
                              timeout=SCRIPT_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, f"{script} took too long")


def cast() -> dict[str, str]:
    path = Path(os.environ.get("COMMS_CAST") or tts_dir() / "cast.tsv")
    out: dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0].strip():
                out[parts[0].strip()] = parts[1].strip()
    except OSError:
        pass
    return out


def volume() -> float:
    try:
        m = re.search(r"^volume=([0-9.]+)$", (comms_mod.state_dir() / "volume.conf").read_text(), re.M)
        v = float(m.group(1)) if m else 1.0
    except (OSError, ValueError):
        return 1.0
    return v if 0 <= v <= 1 else 1.0


def _hold_until() -> int | None:
    """None: no hold. 0: until resumed. Otherwise the epoch it ends."""
    if not comms_mod.held():
        return None
    try:
        return int("".join(c for c in (comms_mod.state_dir() / "hold").read_text() if c.isdigit()) or "0")
    except (OSError, ValueError):
        return 0


def version() -> str:
    try:
        from importlib.metadata import version as v
        return v("agent-companion")
    except Exception:
        return "unknown"


def _enabled_ids(request: Request) -> list[str]:
    return [f["id"] for f in request.app.state.features]


def _features(request: Request) -> list[dict]:
    on = _enabled_ids(request)
    out = []
    for f in FEATURES:
        now = (on + [f]) if f not in on else on
        now = [x for x in FEATURES if x in now]
        without = [x for x in on if x != f]
        line = 'features = "' + ",".join(without if f in on else now) + '"'
        out.append({"id": f, "enabled": f in on, "line": line})
    return out


def _collab() -> dict:
    def val(k):
        return str(config.get(k, "") or "").strip() or None
    return {"label": val("collab_label"), "from": val("collab_from"), "repo": val("collab_repo")}


def _devices(request: Request) -> list[dict]:
    me = _me(request)
    return [{"name": d["name"], "created": d["created"], "this_device": d["name"] == me}
            for d in auth.devices()]


def _me(request: Request) -> str | None:
    """The paired device making this request, by its key's hash."""
    key = auth._owner_presented(request)
    if not key:
        return None
    h = auth.hash_secret(key)
    import secrets
    for e in auth._read_list(auth.owner_keys_path()):
        if isinstance(e.get("hash"), str) and secrets.compare_digest(h, e["hash"]):
            return e.get("name")
    return None


@router.get("/api/settings", dependencies=[Depends(auth.require_token)])
def get_settings(request: Request):
    return {
        "version": version(),
        "config_path": str(config.path()),
        "features": _features(request),
        "comms": {"installed": piper_installed(), "cast": cast(), "held": comms_mod.held(),
                  "hold_until": _hold_until(), "volume": volume()},
        "devices": _devices(request),
        "collab": _collab(),
        "docs": {n: None for n in DOCS},
    }


class Hold(BaseModel):
    minutes: int | None = None


@router.post("/api/settings/comms/hold", dependencies=[Depends(auth.require_write)])
def hold(body: Hold):
    args = ["hold"]
    if body.minutes is not None:
        if not 0 < body.minutes <= 1440:
            raise HTTPException(400, "minutes must be 1 to 1440")
        args.append(str(body.minutes))
    r = _run("quiet.sh", *args)
    if r.returncode != 0:
        raise HTTPException(500, (r.stderr or "quiet.sh failed").strip()[:200])
    return {"ok": True, "held": comms_mod.held()}


@router.post("/api/settings/comms/resume", dependencies=[Depends(auth.require_write)])
def resume():
    r = _run("quiet.sh", "resume")
    if r.returncode != 0:
        raise HTTPException(500, (r.stderr or "quiet.sh failed").strip()[:200])
    return {"ok": True, "held": comms_mod.held()}


class Volume(BaseModel):
    volume: float


@router.post("/api/settings/comms/volume", dependencies=[Depends(auth.require_write)])
def set_volume(body: Volume):
    if not 0 <= body.volume <= 1:
        raise HTTPException(400, "volume is 0 to 1")
    d = comms_mod.state_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / ".volume.conf.tmp"
    tmp.write_text(f"volume={round(body.volume, 2)}\n")
    os.replace(tmp, d / "volume.conf")
    return {"ok": True, "volume": volume()}


class Test(BaseModel):
    name: str


@router.post("/api/settings/comms/test", dependencies=[Depends(auth.require_write)])
def test_clip(body: Test):
    if not piper_installed():
        raise HTTPException(409, COMMS_NOT_INSTALLED)
    if body.name not in cast():
        raise HTTPException(400, "not a voice in the cast")
    r = _run("speak.sh", "Comms test", speaker=body.name)
    if r.returncode != 0:
        raise HTTPException(500, (r.stderr or "speak.sh failed").strip()[:200])
    return {"ok": True}


class Unpair(BaseModel):
    name: str
    confirm: bool = False


@router.post("/api/settings/devices/unpair", dependencies=[Depends(auth.require_write)])
def unpair(body: Unpair, request: Request):
    if body.name == _me(request) and body.confirm is not True:
        raise HTTPException(409, "that is the device you are using: send confirm to unpair it")
    if not auth.remove_device(body.name):
        raise HTTPException(404, "no such device")
    return {"ok": True}
