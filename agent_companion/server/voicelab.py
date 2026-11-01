"""Voice-lab summaries and carefully bounded voice controls."""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response

from .. import agentvibes, auditions, config, safe_paths, voices
from . import auth

TITLE = "Voice lab"
router = APIRouter()
NOSTORE = {"Cache-Control": "no-store"}
DIR = re.compile(r"[A-Za-z0-9_.-]{1,80}")
CLIP = re.compile(r"(?:new|live)-[0-9]+\.mp3")
CONF = re.compile(r"([A-Za-z][A-Za-z0-9_-]{0,31})\s*=\s*([A-Za-z0-9_-]{1,64})")
STAGES = re.compile(r"\bSTAGES\s*=\s*(\[[^\]]*\])", re.S)
_test_lock = threading.Lock()
RUNNING_S = 15 * 60
TEST_STOCK = "Testing, testing. This is how I sound right now."


def tts_dir() -> Path:
    raw = config.get("tts_dir")
    return Path(str(raw)).expanduser() if raw else Path.home() / ".local" / "share" / "agent-companion" / "tts"


def lab_root() -> Path:
    raw = config.get("voicelab_dir")
    return Path(str(raw)).expanduser() if raw else tts_dir() / "voicelab" / "builds"


def configured() -> bool:
    return agentvibes.available(tts_dir())


# --- the voicelab/ pipeline, for an install with no coordinator ---
# What voicelab/prep.py, train.sh and serve.py leave under voices_dir. Training
# and serving happen from the shell (voicelab/README.md), never from this page.
VOICE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
WEIGHTS = (".ckpt", ".pth")
AUDIO = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".flac": "audio/flac"}


def voices_dir() -> Path:
    raw = config.env("VOICES_DIR") or config.get("voices_dir")
    return Path(str(raw)).expanduser() if raw else Path.home() / ".local/share/agent-companion/voices"


def _size(d: Path) -> int:
    total = 0
    for p in d.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def _stage(d: Path) -> str:
    if (d / "voice.json").is_file():
        return "served"
    if any(p.suffix in WEIGHTS for p in d.rglob("*") if p.is_file()):
        return "trained"
    return "prepped"


def _pipeline_row(d: Path) -> dict:
    aud = d / "audition"
    clips = sorted(p.name for p in aud.iterdir() if p.is_file() and p.suffix.lower() in AUDIO) if aud.is_dir() else []
    return {"name": d.name, "stage": _stage(d), "bytes": _size(d), "audition": clips}


def pipeline_summary() -> dict:
    root = voices_dir()
    dirs = sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink() and VOICE_NAME.match(p.name)) \
        if root.is_dir() else []
    rows = [_pipeline_row(d) for d in dirs]
    text = (f"{len(rows)} voice{'' if len(rows) == 1 else 's'} in {root}" if rows
            else f"No voices yet: voicelab/README.md says how to make one (they land in {root}).")
    why = "Tiers and test lines need a speech coordinator; this install gives a speaker its voice in comms/cast.tsv."
    return {"now": {"kind": "pipeline", "text": text},
            "needs": {"ok": True, "items": []},
            "running": {"ok": True, "builds": []},
            "voices": {"ok": False, "voices": [], "why": why},
            "other": {"ok": True, "builds": []},
            "pipeline": {"root": str(root), "voices": rows}}


def _need_tts() -> Path:
    tts = tts_dir()
    if not agentvibes.available(tts):
        raise HTTPException(503, "comms is not set up: set tts_dir to the coordinator directory")
    return tts


def stages(tts: Path) -> list[str] | None:
    """Read a literal stage list without importing coordinator code."""
    try:
        text = (Path(tts) / "voicelab" / "voicelab.py").read_text(errors="replace")
        match = STAGES.search(text)
        values = ast.literal_eval(match.group(1)) if match else None
    except (OSError, ValueError, SyntaxError):
        return None
    return values if isinstance(values, list) and values and all(isinstance(value, str) and DIR.fullmatch(value) for value in values) else None


def is_build_dir(name: str) -> bool:
    return bool(DIR.fullmatch(name)) and name not in (".", "..")


def _tail(path: Path) -> tuple[str | None, float | None]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - 4096))
            lines = [line.strip() for line in handle.read().decode("utf-8", "replace").splitlines() if line.strip()]
            return (lines[-1] if lines else None, path.stat().st_mtime)
    except OSError:
        return None, None


def _clips(directory: Path) -> dict:
    out = {"new": [], "live": []}
    try:
        found = []
        for name in os.listdir(directory / "audition"):
            match = CLIP.fullmatch(name)
            if match:
                kind, number = name[:-4].split("-", 1)
                found.append((kind, int(number), name))
    except OSError:
        return out
    for kind, _number, name in sorted(found):
        out[kind].append(name)
    return out


def decision_of(directory: Path) -> dict | None:
    try:
        value = json.loads((directory / "decision.json").read_text())
        at = value.get("at") if isinstance(value, dict) else None
        if isinstance(value, dict) and isinstance(value.get("want"), bool):
            return {"want": value["want"], "at": float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) else None}
    except (OSError, ValueError, TypeError):
        pass
    return None


def decide(lab: Path, directory: str, want: bool, now: float | None = None) -> None:
    if not is_build_dir(directory):
        raise ValueError("not a build")
    target = safe_paths.directory(lab, directory)
    if target is None:
        raise ValueError("not a build")
    target = target / "decision.json"
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(json.dumps({"want": bool(want), "at": time.time() if now is None else now}))
    os.replace(temporary, target)


def _scores(directory: Path) -> dict:
    try:
        rank = json.loads((directory / "scores.json").read_text()).get("rank")
    except (OSError, ValueError, AttributeError):
        return {"best": None, "baseline": None, "why": "scores could not be read"}
    if not isinstance(rank, list):
        return {"best": None, "baseline": None, "why": "scores have no ranking"}
    def take(item):
        if not isinstance(item, dict):
            return None
        out = {"tag": str(item.get("tag") or "")}
        for key in ("ecapa", "wer"):
            if isinstance(item.get(key), (int, float)) and not isinstance(item[key], bool):
                out[key] = round(float(item[key]), 3)
        return out
    return {"best": take(next((item for item in rank if isinstance(item, dict) and item.get("tag") != "live"), None)),
            "baseline": take(next((item for item in rank if isinstance(item, dict) and item.get("tag") == "live"), None))}


def _zero_shot(value: dict) -> str | None:
    engine = value.get("engine")
    if not isinstance(engine, str) or "zero-shot" not in engine.lower():
        return None
    return re.sub(r"(?i)\s*zero-shot\s*", " ", engine).strip()[:40] or "zero-shot"


def _build(directory: Path, stage_list: list[str]) -> dict | None:
    try:
        value = json.loads((directory / "voice.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    name = str(value.get("Name") or value.get("name") or directory.name)
    if not auditions.NAME.fullmatch(name):
        name = directory.name
    line, line_at = _tail(directory / "run.log")
    zero = _zero_shot(value)
    if zero:
        try:
            audition_at = (directory / ".done-audition").stat().st_mtime
        except OSError:
            audition_at = None
        return {"dir": directory.name, "name": name, "engine": zero, "zero_shot": True, "stages": [],
                "next": None if audition_at else "audition", "step": None if audition_at else 1, "of": 1,
                "line": line, "line_at": line_at, "active_at": max(audition_at or 0, line_at or 0) or None,
                "scores": None, "clips": _clips(directory), "live_now": False, "decision": decision_of(directory)}
    marks, latest = [], line_at or 0
    for stage in stage_list:
        marker = directory / f".done-{stage}"
        try:
            marker_at = marker.stat().st_mtime
            stamp = marker.read_text(errors="replace").strip()[:19]
            marks.append({"stage": stage, "done": True, "at": stamp if re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d", stamp) else None})
            latest = max(latest, marker_at)
        except OSError:
            marks.append({"stage": stage, "done": False, "at": None})
    next_index = next((index for index, mark in enumerate(marks) if not mark["done"]), None)
    return {"dir": directory.name, "name": name, "stages": marks,
            "next": marks[next_index]["stage"] if next_index is not None else None,
            "step": next_index + 1 if next_index is not None else None, "of": len(stage_list), "line": line,
            "line_at": line_at, "active_at": latest or None, "scores": _scores(directory),
            "clips": _clips(directory), "live_now": False, "decision": decision_of(directory)}


def builds(tts: Path, lab: Path, now: float) -> dict:
    stage_list = stages(tts)
    if stage_list is None:
        return {"ok": False, "why": "could not read the voice lab stage list"}
    try:
        directories = sorted(path for path in Path(lab).iterdir() if path.is_dir() and is_build_dir(path.name))
    except OSError:
        return {"ok": False, "why": "could not read the voice lab directory"}
    rows = [row for row in (_build(path, stage_list) for path in directories) if row]
    rows.sort(key=lambda row: -(row["active_at"] or 0))
    return {"ok": True, "builds": rows}


def phase(build: dict, now: float) -> str:
    if build.get("next") is None:
        return "finished" if build.get("clips", {}).get("new") else "stopped"
    return "running" if build.get("active_at") and now - build["active_at"] < RUNNING_S else "stopped"


def tiers(tts: Path) -> dict[str, tuple[int, str]]:
    out = {}
    try:
        lines = (Path(tts) / "tiers.conf").read_text(errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        key, equals, value = line.partition("=")
        if equals and key.strip() and value.strip() in ("1", "2", "3"):
            out[key.strip().lower()] = (int(value.strip()), "tiers.conf")
    return out


def cast(tts: Path, now: float | None = None) -> dict:
    try:
        lines = (Path(tts) / "remote-voices.conf").read_text(errors="replace").splitlines()
    except OSError:
        return {"ok": False, "why": "could not read the configured cast"}
    tier, result = tiers(tts), []
    for line in lines:
        match = CONF.fullmatch(line.strip())
        if not match:
            continue
        name, voice = match.groups()
        level = tier.get(name.lower())
        result.append({"name": name, "voice": voice, "engine": "configured", "words": "Configured voice",
                       "tier": level[0] if level else 2, "tier_from": level[1] if level else "default", "changes": []})
    return {"ok": True, "voices": result}


def cast_name(tts: Path, name: str) -> str | None:
    return next((row["name"] for row in cast(tts).get("voices", []) if row["name"].lower() == str(name).lower()), None)


def voice_of(directory: str, rows: list[dict]) -> str | None:
    known = {}
    for row in rows:
        known.setdefault(str(row.get("voice") or "").lower(), row.get("voice"))
        known.setdefault(str(row.get("name") or "").lower(), row.get("voice"))
    base = re.sub(r"-(?:index|gsv)$", "", str(directory).lower())
    for candidate in (base, base.split("-", 1)[0]):
        for value in (candidate, re.sub(r"\d+$", "", candidate)):
            if value in known and value:
                return known[value]
    return None


def fold(rows: list[dict]) -> list[dict]:
    by = {}
    for row in rows:
        voice = row.get("voice")
        if voice not in by:
            by[voice] = dict(row, aliases=[])
        elif row["name"].lower() == str(voice).lower() and by[voice]["name"].lower() != str(voice).lower():
            by[voice] = dict(row, aliases=[by[voice]["name"]] + by[voice]["aliases"])
        else:
            by[voice]["aliases"].append(row["name"])
    return list(by.values())


def verdict(build: dict, name: str) -> str:
    if build.get("zero_shot"):
        return f"{build.get('engine') or 'Zero-shot'} copy, not scored"
    scores = build.get("scores") or {}
    best, live = scores.get("best") or {}, scores.get("baseline") or {}
    if "ecapa" not in best:
        return scores.get("why") or "Not scored yet"
    score = round(best["ecapa"] * 100)
    if "ecapa" not in live:
        result = f"Sounds like {name} {score}"
    else:
        baseline = round(live["ecapa"] * 100)
        result = f"New sounds more like {name}: {score} against live {baseline}" if score - baseline >= 5 else f"A wash: new {score}, live {baseline}"
    return result + (f"; words misheard {round(best['wer'] * 100)}%" if "wer" in best else "")


def summary(tts: Path, lab: Path, voice_state: dict | None, now: float) -> dict:
    built, cast_data = builds(tts, lab, now), cast(tts, now)
    build_rows, cast_rows = built.get("builds", []), fold(cast_data.get("voices", []))
    names = {row["voice"]: row["name"] for row in cast_rows}
    own, other, running = {row["voice"]: [] for row in cast_rows}, [], []
    for row in build_rows:
        voice = voice_of(row["dir"], cast_data.get("voices", []))
        row["phase"], row["verdict"] = phase(row, now), verdict(row, names.get(voice, row["name"]))
        if row["phase"] == "running":
            running.append(dict(row, voice_name=names.get(voice, row["name"])))
        (own[voice] if voice in own else other).append(row)
    needs = [{"kind": "audition", "name": item["name"], "note": item["note"], "clips": item["clips"]}
             for item in auditions.listing(tts) if not item["decision"]]
    voice_rows = []
    for row in cast_rows:
        entries = own[row["voice"]]
        newest = next((entry for entry in entries if entry["phase"] == "finished"), None)
        waiting = bool(newest and newest["decision"] is None and not newest["live_now"])
        if waiting:
            needs.append({"kind": "build", "voice": row["voice"], "name": row["name"], "dir": newest["dir"],
                          "engine": newest.get("engine") or "Trained", "verdict": newest["verdict"], "clips": newest["clips"]})
        voice_rows.append(dict(row, waiting=waiting, builds=entries, notes=[]))
    voice_rows.sort(key=lambda row: (not row["waiting"], row["tier"], row["name"].lower()))
    current = "free" if voice_state and voice_state.get("real") else "voice host status is not known"
    return {"now": {"kind": "free" if current == "free" else "unknown", "text": current},
            "needs": {"ok": True, "items": needs},
            "running": {"ok": bool(built.get("ok")), "builds": running, **({"why": built["why"]} if not built.get("ok") else {})},
            "voices": {"ok": bool(cast_data.get("ok")), "voices": voice_rows, **({"why": cast_data["why"]} if not cast_data.get("ok") else {})},
            "other": {"ok": bool(built.get("ok")), "builds": other}}


class TestLineError(Exception):
    __test__ = False


def test_text(value) -> str:
    if value is None:
        return TEST_STOCK
    if not isinstance(value, str):
        raise ValueError("text must be words")
    value = value.strip()
    if len(value) > 200:
        raise ValueError("at most 200 characters")
    return value or TEST_STOCK


def test_line(tts: Path, name: str, text: str, run=subprocess.run) -> tuple[bytes, str]:
    script, temporary = Path(tts) / "speak.sh", tempfile.mkdtemp(prefix="voice-lab-test-")
    try:
        wav = Path(temporary) / "clip.wav"
        try:
            result = run([str(script), text], env={**os.environ, "TTS_SPEAKER": name, "TTS_RENDER_TO": str(wav),
                                                  "TTS_FLUSHING": "1", "TTS_NO_OPENER": "1"}, timeout=120,
                         capture_output=True, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise TestLineError("the voice took too long") from None
        except OSError as exc:
            raise TestLineError(f"could not run speech: {exc}") from None
        if result.returncode:
            raise TestLineError(f"speech exited {result.returncode}")
        data = wav.read_bytes() if wav.is_file() else b""
        if not data:
            raise TestLineError("speech made no sound")
        return data, "coordinator"
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


@router.get("/api/voicelab", dependencies=[Depends(auth.require_token)])
def get_summary():
    if not configured():
        return JSONResponse(pipeline_summary(), headers=NOSTORE)
    tts = _need_tts()
    return JSONResponse(summary(tts, lab_root(), voices.state(tts), time.time()), headers=NOSTORE)


@router.post("/api/voicelab/tier/{name}", dependencies=[Depends(auth.require_write)])
def set_tier(name: str, body: dict = Body(...)):
    tts, tier = _need_tts(), body.get("tier")
    if not auditions.NAME.fullmatch(name) or type(tier) is not int or tier not in (1, 2, 3):
        raise HTTPException(400, "a voice name and a tier of 1, 2 or 3 are required")
    canonical = cast_name(tts, name)
    if canonical is None:
        raise HTTPException(404, "no such voice in the cast")
    auditions.set_tier(tts, canonical, tier)
    return JSONResponse({"ok": True}, headers=NOSTORE)


@router.post("/api/voicelab/decide/{directory}", dependencies=[Depends(auth.require_write)])
def decide_build(directory: str, body: dict = Body(...)):
    _need_tts()
    want, lab = body.get("want"), lab_root()
    target = safe_paths.directory(lab, directory) if is_build_dir(directory) else None
    if not isinstance(want, bool):
        raise HTTPException(400, "want must be true or false")
    if target is None or not (target / "voice.json").is_file():
        raise HTTPException(404, "no such build")
    previous = decision_of(target)
    try:
        decide(lab, directory, want)
    except ValueError:
        raise HTTPException(404, "no such build") from None
    return JSONResponse({"ok": True, "filed": False, "detail": "already filed" if want and previous and previous.get("want") else ""}, headers=NOSTORE)


@router.post("/api/voicelab/test/{name}", dependencies=[Depends(auth.require_write)])
def render_test(name: str, body: dict = Body(...)):
    tts = _need_tts()
    if not auditions.NAME.fullmatch(name):
        raise HTTPException(400, "not a voice name")
    try:
        text = test_text(body.get("text"))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    canonical = cast_name(tts, name)
    if canonical is None:
        raise HTTPException(404, "no such voice in the cast")
    if not _test_lock.acquire(blocking=False):
        raise HTTPException(409, "another test line is rendering")
    try:
        data, by = test_line(tts, canonical, text)
    except TestLineError as exc:
        raise HTTPException(502, str(exc))
    finally:
        _test_lock.release()
    return Response(data, media_type="audio/wav", headers={**NOSTORE, "X-Rendered-By": by})


@router.get("/api/voicelab/clip/{directory}/{clip}", dependencies=[Depends(auth.require_token)])
def get_clip(directory: str, clip: str):
    _need_tts()
    if not is_build_dir(directory) or not CLIP.fullmatch(clip):
        raise HTTPException(404, "no such clip")
    base = safe_paths.directory(lab_root(), directory)
    path = safe_paths.file(lab_root(), Path(directory) / "audition" / clip) if base else None
    if base is None or not (base / "voice.json").is_file() or path is None:
        raise HTTPException(404, "no such clip")
    return FileResponse(path, media_type="audio/mpeg", headers=NOSTORE)


@router.get("/api/voicelab/audition/{name}/{file:path}", dependencies=[Depends(auth.require_token)])
def pipeline_audition(name: str, file: str):
    path = safe_paths.file(voices_dir() / name / "audition", file) if VOICE_NAME.match(name) else None
    if path is None or path.suffix.lower() not in AUDIO:
        raise HTTPException(404, "no such clip")
    return FileResponse(path, media_type=AUDIO[path.suffix.lower()], headers=NOSTORE)
