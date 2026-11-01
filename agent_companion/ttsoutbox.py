"""Files shared by a speech coordinator and its players.

The coordinator writes a numbered ``.json`` metadata file and an ``.opus``
clip.  Players list, take and acknowledge clips here.  Every operation is
safe when the optional coordinator has not created its outbox yet.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path

from . import safe_paths

KEEP_S = 24 * 3600
PLAYERS = ("phone", "web", "app")
RESULTS = ("played", "skipped", "failed")
PLAYER_S = 45
TAKEN_S = 90
PUSH_GRACE_S = 20
APP_LEAD_S = 3
APP_LIVE_S = 5
_take_lock = threading.Lock()


def _dir(root: Path) -> Path:
    return Path(root)


def seen(root: Path, now: float | None = None) -> None:
    """Record that a player is listening."""
    d = _dir(root)
    d.mkdir(parents=True, exist_ok=True)
    mark = d / ".seen"
    mark.touch()
    if now is not None:
        os.utime(mark, (now, now))


def top(root: Path) -> int:
    try:
        return int((_dir(root) / ".seq").read_text().strip() or 0)
    except (OSError, ValueError):
        return 0


def _answers(d: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    for done in d.glob("*.done") if d.is_dir() else []:
        try:
            answer = json.loads(done.read_text())
            take = json.loads((d / f"{done.stem}.taken").read_text())
            who, at = str(take.get("who") or ""), float(answer.get("at") or 0)
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        if who:
            out[who] = max(out.get(who, 0), at)
    return out


def _taken_by_other(d: Path, seq: int, who: str, now: float, answers: dict[str, float] | None = None) -> bool:
    try:
        take = json.loads((d / f"{seq}.taken").read_text())
        by, at = str(take.get("who") or ""), float(take.get("at") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return False
    if not by or by == who:
        return False
    return now - max(at, (answers or _answers(d)).get(by, 0)) < TAKEN_S


def _app_waiting(d: Path, seq: int, now: float) -> bool:
    if (d / f"{seq}.taken").exists():
        return False
    try:
        pushed = json.loads((d / f"{seq}.pushed").read_text())
        return now - float(pushed.get("at") or 0) < PUSH_GRACE_S
    except (OSError, ValueError, TypeError, AttributeError):
        try:
            listening = now - (d / ".app").stat().st_mtime < APP_LIVE_S
            fresh = now - (d / f"{seq}.json").stat().st_mtime < APP_LEAD_S
            return listening and fresh
        except OSError:
            return False


def _busy(d: Path, who: str, now: float, answers: dict[str, float]) -> bool:
    for taken in d.glob("*.taken") if d.is_dir() else []:
        try:
            seq = int(taken.stem)
        except ValueError:
            continue
        if not (d / f"{seq}.done").exists() and _taken_by_other(d, seq, who, now, answers):
            return True
    return False


def pending(after: int, root: Path, who: str = "phone", now: float | None = None) -> list[dict]:
    """Unanswered clips after ``after``, oldest first unless urgent."""
    d, now = _dir(root), time.time() if now is None else now
    if who not in PLAYERS or not d.is_dir():
        return []
    answers = _answers(d)
    if _busy(d, who, now, answers):
        return []
    out = []
    for meta_file in d.glob("*.json"):
        try:
            seq = int(meta_file.stem)
            if now - meta_file.stat().st_mtime > KEEP_S:
                for suffix in (".json", ".opus", ".done", ".taken", ".pushed"):
                    (d / f"{seq}{suffix}").unlink(missing_ok=True)
                continue
            if seq <= after or (d / f"{seq}.done").exists() or not (d / f"{seq}.opus").is_file():
                continue
            if _taken_by_other(d, seq, who, now, answers) or (who != "app" and _app_waiting(d, seq, now)):
                continue
            meta = json.loads(meta_file.read_text())
            if not isinstance(meta, dict) or (meta.get("probe") and who == "phone"):
                continue
            out.append({"seq": seq, "name": str(meta.get("name") or ""),
                        "duration": float(meta.get("duration") or 0),
                        "age": round(max(0, now - float(meta.get("at") or now)), 1),
                        "urgent": bool(meta.get("urgent")), "probe": bool(meta.get("probe"))})
        except (OSError, ValueError, TypeError):
            continue
    return sorted(out, key=lambda clip: (not clip["urgent"], clip["seq"]))


def claim(player_id: str, root: Path, now: float | None = None) -> None:
    d = _dir(root)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / ".player.tmp"
    tmp.write_text(json.dumps({"id": str(player_id)[:64], "at": time.time() if now is None else now}))
    os.replace(tmp, d / ".player")


def release(player_id: str, root: Path) -> None:
    try:
        mark = _dir(root) / ".player"
        if json.loads(mark.read_text()).get("id") == str(player_id)[:64]:
            mark.unlink()
    except (OSError, ValueError, AttributeError):
        pass


def web_player(root: Path, now: float | None = None) -> str | None:
    try:
        item = json.loads((_dir(root) / ".player").read_text())
        if (time.time() if now is None else now) - float(item.get("at") or 0) < PLAYER_S:
            return str(item.get("id") or "") or None
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def app_listening(root: Path, now: float | None = None) -> None:
    d = _dir(root)
    d.mkdir(parents=True, exist_ok=True)
    mark = d / ".app"
    mark.touch()
    if now is not None:
        os.utime(mark, (now, now))


def pushed(seq: int, root: Path, now: float | None = None) -> None:
    try:
        (_dir(root) / f"{int(seq)}.pushed").write_text(json.dumps({"at": time.time() if now is None else now}))
    except OSError:
        pass


def clip(seq: int, root: Path) -> Path | None:
    return safe_paths.file(_dir(root), f"{int(seq)}.opus")


def take(seq: int, who: str, root: Path, now: float | None = None) -> bool:
    if who not in PLAYERS:
        return False
    d, now = _dir(root), time.time() if now is None else now
    with _take_lock:
        # An answered clip is anyone's to replay (the chat tab's replay); only
        # one still being played is held against a second player.
        held = not (d / f"{int(seq)}.done").exists() and _taken_by_other(d, int(seq), who, now)
        if held or (who != "app" and _app_waiting(d, int(seq), now)):
            return False
        try:
            (d / f"{int(seq)}.taken").write_text(json.dumps({"who": who, "at": now}))
        except OSError:
            return False
    return True


def mark(seq: int, result: str, detail: str, root: Path) -> bool:
    d, seq = _dir(root), int(seq)
    if result not in RESULTS or not (d / f"{seq}.json").is_file():
        return False
    tmp = d / f".{seq}.done.tmp"
    try:
        tmp.write_text(json.dumps({"result": result, "detail": str(detail)[:200], "at": time.time()}))
        os.replace(tmp, d / f"{seq}.done")
    except OSError:
        return False
    return True


def replay(seq: int, root: Path, now: float | None = None) -> int | None:
    d, seq = _dir(root), int(seq)
    source, audio = d / f"{seq}.json", d / f"{seq}.opus"
    try:
        meta = json.loads(source.read_text())
        if not isinstance(meta, dict) or meta.get("probe") or not audio.is_file():
            return None
    except (OSError, ValueError):
        return None
    new = top(d) + 1
    try:
        (d / ".seq").write_text(f"{new}\n")
        shutil.copyfile(audio, d / f"{new}.opus")
        meta.update(seq=new, at=time.time() if now is None else now, replay_of=seq)
        (d / f"{new}.json").write_text(json.dumps(meta))
    except OSError:
        return None
    return new


def index(root: Path) -> list[tuple[int, str, str, float]]:
    out = []
    for path in _dir(root).glob("*.json") if _dir(root).is_dir() else []:
        try:
            meta = json.loads(path.read_text())
            if not isinstance(meta, dict) or meta.get("probe"):
                continue
            text = "".join(c for c in str(meta.get("text") or "").lower() if c.isalnum())
            out.append((int(path.stem), str(meta.get("name") or ""), text, float(meta.get("at") or 0)))
        except (OSError, ValueError, TypeError):
            continue
    return out


def seq_for(name: str | None, text: str | None, at: float | None, items) -> int | None:
    words = "".join(c for c in str(text or "").lower() if c.isalnum())
    if not name or len(words) < 12:
        return None
    found = [seq for seq, speaker, queued, queued_at in items
             if speaker == name and queued and (words in queued or queued in words)
             and (at is None or -60 <= at - queued_at <= 600)]
    return max(found) if found else None
