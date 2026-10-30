"""Optional controls for a speech coordinator.

The coordinator directory is supplied by the server setting.  This module
only parses or rewrites its documented text files.  It never imports, executes
or trusts a coordinator configuration as code.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,31}$")
VOICE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}(?::[0-9]{1,3})?$")
TRACK = re.compile(r"^[A-Za-z0-9_.-]{1,120}\.(?:mp3|ogg|opus|wav)$")
PRESETS = {"plain": "", "warm": "reverb 30", "bright": "treble 4", "deep": "pitch -150", "distant": "reverb 60 gain -2"}
ANNOUNCEMENTS = ("prompt", "subagent", "complete")
MAX_MUTE_MINUTES = 24 * 60


def _tts(tts: Path) -> Path:
    return Path(tts)


def _base(tts: Path) -> Path:
    return _tts(tts).parent


def _config(tts: Path) -> Path:
    return _base(tts) / "config"


def available(tts: Path) -> bool:
    """Whether the coordinator has the control files this overlay needs."""
    return _tts(tts).is_dir() and (_config(tts) / "audio-effects.cfg").is_file()


def _atomic(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(body)
        os.replace(name, path)
    except BaseException:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def _effects_path(tts: Path) -> Path:
    return _config(tts) / "audio-effects.cfg"


def _override_path(tts: Path) -> Path:
    return _tts(tts) / "voices.override.tsv"


def _effects_override_path(tts: Path) -> Path:
    return _tts(tts) / "effects.override.tsv"


def _rows(path: Path) -> dict[str, str]:
    out = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#") or "\t" not in line:
            continue
        key, value = line.split("\t", 1)
        if NAME.fullmatch(key.strip()):
            out[key.strip()] = value.strip()
    return out


def _write_rows(path: Path, values: dict[str, str]) -> None:
    _atomic(path, "".join(f"{name}\t{value}\n" for name, value in sorted(values.items())))


def voices(tts: Path) -> list[dict]:
    """Installed voice models, never a package's list of downloadable ones."""
    out = []
    try:
        models = sorted((_base(tts) / "piper-voices").glob("*.onnx"))
    except OSError:
        return out
    for model_path in models:
        try:
            meta = json.loads((model_path.parent / f"{model_path.name}.json").read_text())
            count = int(meta.get("num_speakers") or 1) if isinstance(meta, dict) else 1
        except (OSError, ValueError, TypeError):
            continue
        labels = {}
        if isinstance(meta.get("speaker_id_map"), dict):
            for label, number in meta["speaker_id_map"].items():
                try:
                    labels[int(number)] = str(label)
                except (TypeError, ValueError):
                    pass
        model = model_path.name[:-5]
        for number in range(max(1, count)):
            multi = count > 1
            out.append({"id": f"{model}:{number}" if multi else model, "model": model,
                        "speaker": number if multi else None,
                        "label": labels.get(number, f"{model} #{number}") if multi else model,
                        "multi": multi})
    return out


def tracks(tts: Path) -> list[str]:
    try:
        return sorted(path.name for path in (_base(tts) / "audio" / "tracks").glob("*.mp3"))
    except OSError:
        return []


def _preset(effects: str) -> str:
    bare = re.sub(r"^\s*gain\s+-?\d+(?:\.\d+)?\s*", "", effects or "").strip()
    return next((key for key, value in PRESETS.items() if value == bare), "custom")


def speakers(tts: Path) -> list[dict]:
    overrides, effects = _rows(_override_path(tts)), _rows(_effects_override_path(tts))
    out = []
    try:
        lines = _effects_path(tts).read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 4 or not NAME.fullmatch(parts[0]):
            continue
        try:
            volume = float(parts[3])
        except ValueError:
            volume = 0.7
        source = effects.get(parts[0], parts[1])
        out.append({"name": parts[0], "effects": parts[1], "preset": _preset(source),
                    "track": parts[2], "volume": volume, "voice": overrides.get(parts[0], "")})
    return out


def music_enabled(tts: Path) -> bool:
    try:
        return (_config(tts) / "background-music-enabled.txt").read_text().strip().lower() == "true"
    except OSError:
        return False


def set_music_enabled(tts: Path, enabled: bool) -> bool:
    _atomic(_config(tts) / "background-music-enabled.txt", "true\n" if enabled else "false\n")
    return bool(enabled)


def update_speaker(tts: Path, name: str, preset: str | None = None, track: str | None = None,
                   volume: float | None = None, voice: str | None = None) -> dict:
    if not NAME.fullmatch(name or ""):
        raise ValueError("invalid speaker name")
    rows = speakers(tts)
    if name not in {row["name"] for row in rows}:
        raise ValueError("unknown speaker")
    if preset is not None and preset not in PRESETS:
        raise ValueError("unknown preset")
    if track is not None and track not in tracks(tts):
        raise ValueError("unknown track")
    if volume is not None:
        try:
            volume = float(volume)
        except (TypeError, ValueError):
            raise ValueError("volume must be a number") from None
        if not 0 <= volume <= 1:
            raise ValueError("volume must be between 0 and 1")
    if voice is not None:
        voice = str(voice)
        if voice and (not VOICE.fullmatch(voice) or voice not in {item["id"] for item in voices(tts)}):
            raise ValueError("voice is not installed")
        values = _rows(_override_path(tts))
        if voice:
            values[name] = voice
        else:
            values.pop(name, None)
        _write_rows(_override_path(tts), values)
    if preset is not None:
        values = _rows(_effects_override_path(tts))
        values[name] = PRESETS[preset]
        _write_rows(_effects_override_path(tts), values)
    updated, lines = {}, []
    for row in rows:
        item = dict(row)
        if item["name"] == name:
            if preset is not None:
                item["effects"] = (f"gain {levels(tts)['voice_gain']} {PRESETS[preset]}").strip()
                item["preset"] = preset
            if track is not None:
                item["track"] = track
            if volume is not None:
                item["volume"] = volume
            if voice is not None:
                item["voice"] = voice
            updated = item
        lines.append(f"{item['name']}|{item['effects']}|{item['track']}|{item['volume']:.2f}")
    _atomic(_effects_path(tts), "\n".join(lines) + "\n")
    return updated


def _values(tts: Path) -> dict[str, str]:
    out = {}
    try:
        lines = (_tts(tts) / "announce.conf").read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip()
    return out


def _write_values(tts: Path, values: dict[str, str]) -> None:
    _atomic(_tts(tts) / "announce.conf", "".join(f"{key}={value}\n" for key, value in values.items()))


def subtitles(tts: Path) -> bool:
    value = _values(tts).get("subtitles")
    return value is None or value.lower() in ("on", "true", "yes", "1")


def set_subtitles(tts: Path, enabled: bool) -> bool:
    values = _values(tts)
    values["subtitles"] = "on" if enabled else "off"
    _write_values(tts, values)
    return bool(enabled)


def announcements(tts: Path) -> dict[str, bool]:
    values = _values(tts)
    return {kind: values.get(kind, "on").lower() in ("on", "true", "yes", "1") for kind in ANNOUNCEMENTS}


def set_announcement(tts: Path, kind: str, enabled: bool) -> dict[str, bool]:
    if kind not in ANNOUNCEMENTS:
        raise ValueError("unknown announcement")
    values, current = _values(tts), announcements(tts)
    current[kind] = bool(enabled)
    for key, value in current.items():
        values[key] = "on" if value else "off"
    _write_values(tts, values)
    return current


def _expiry(path: Path) -> int | None:
    try:
        digits = "".join(char for char in path.read_text() if char.isdigit())
        value = int(digits or "0")
    except (OSError, ValueError):
        return None
    return value if value == 0 or value > time.time() else None


def mute_state(tts: Path) -> dict:
    directory = _tts(tts)
    muted = {}
    for path in directory.glob("mute-*") if directory.is_dir() else []:
        name, until = path.name[5:], _expiry(path)
        if NAME.fullmatch(name) and until is not None:
            muted[name] = until
    return {"all": _expiry(directory / "mute"), "speakers": muted}


def set_mute(tts: Path, scope: str, minutes: int | None) -> dict:
    if scope != "all" and scope not in {row["name"] for row in speakers(tts)}:
        raise ValueError("unknown speaker")
    if minutes is not None and (type(minutes) is not int or minutes < 0 or minutes > MAX_MUTE_MINUTES):
        raise ValueError(f"minutes must be between 0 and {MAX_MUTE_MINUTES}")
    path = _tts(tts) / ("mute" if scope == "all" else f"mute-{scope}")
    if minutes == 0:
        path.unlink(missing_ok=True)
    else:
        _atomic(path, f"{0 if minutes is None else int(time.time()) + minutes * 60}\n")
    return mute_state(tts)


def levels(tts: Path) -> dict:
    defaults = {"voice_gain": 8.0, "music_volume": 0.7}
    try:
        lines = (_tts(tts) / "levels.conf").read_text().splitlines()
    except OSError:
        return defaults
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key in defaults:
            try:
                defaults[key] = float(value)
            except ValueError:
                pass
    return defaults


def set_level(tts: Path, key: str, value, speaker: str | None = None) -> dict:
    if key not in ("voice_gain", "music_volume"):
        raise ValueError("unknown level")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("level must be a number") from None
    if not 0 <= number <= 100:
        raise ValueError("level must be between 0 and 100")
    if speaker is not None and speaker not in {row["name"] for row in speakers(tts)}:
        raise ValueError("unknown speaker")
    values = levels(tts)
    values[key] = number
    _atomic(_tts(tts) / "levels.conf", "".join(f"{name}={amount:g}\n" for name, amount in values.items()))
    return levels(tts)


def hold_state(tts: Path) -> dict:
    until = _expiry(_tts(tts) / "hold")
    return {"held": until is not None, "until": until}


def set_hold(tts: Path, minutes: int | None) -> dict:
    if minutes is not None and (type(minutes) is not int or minutes < 0 or minutes > MAX_MUTE_MINUTES):
        raise ValueError(f"minutes must be between 0 and {MAX_MUTE_MINUTES}")
    path = _tts(tts) / "hold"
    if minutes == 0:
        path.unlink(missing_ok=True)
    else:
        _atomic(path, f"{0 if minutes is None else int(time.time()) + minutes * 60}\n")
    return hold_state(tts)


def release_held(tts: Path) -> dict:
    set_hold(tts, 0)
    return {"ok": True, "detail": ""}


def transcript(tts: Path, limit: int = 40) -> list[dict]:
    """Recent coordinator log lines, without executing anything."""
    try:
        lines = (_tts(tts) / "speak.log").read_text(errors="replace").splitlines()[-max(1, limit * 4):]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        match = re.match(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s+(?:SPOKE|PLAY-FAIL)\s+([^:]+?)(?:\s+\d+(?:\.\d+)?s)?\s*::\s*(.*)$", line)
        if not match:
            continue
        try:
            at = time.mktime(time.strptime(match.group(1), "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            continue
        out.append({"at": at, "name": match.group(2).strip()[:80], "text": match.group(3).strip()[:2000], "state": "spoke"})
        if len(out) >= limit:
            break
    return out


def snapshot(tts: Path) -> dict:
    return {"ok": True, "setup": True, "speakers": speakers(tts), "voices": voices(tts),
            "music_enabled": music_enabled(tts), "subtitles": subtitles(tts),
            "announcements": announcements(tts), "mute": mute_state(tts),
            "levels": levels(tts), "hold": hold_state(tts), "tracks": tracks(tts)}


def stop_playback(tts: Path) -> dict:
    script = _tts(tts) / "stop.sh"
    if not script.is_file() or not os.access(script, os.X_OK):
        return {"stopped": False, "detail": "stop is unavailable"}
    try:
        result = subprocess.run([str(script)], timeout=12, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return {"stopped": False, "detail": "stop timed out"}
    except OSError as exc:
        return {"stopped": False, "detail": f"could not run stop: {exc}"}
    return {"stopped": result.returncode == 0, "detail": "" if result.returncode == 0 else "stop failed"}


def speak_test(tts: Path) -> dict:
    script = _tts(tts) / "speak.sh"
    if not script.is_file() or not os.access(script, os.X_OK):
        return {"ok": False, "detail": "test speech is unavailable"}
    try:
        result = subprocess.run([str(script), "This is a test message."], timeout=30, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": "test speech timed out"}
    except OSError as exc:
        return {"ok": False, "detail": f"could not start test speech: {exc}"}
    return {"ok": result.returncode == 0, "detail": "" if result.returncode == 0 else "test speech failed"}
