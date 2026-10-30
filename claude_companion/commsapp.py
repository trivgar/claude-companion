"""Optional Android player registration and Firebase delivery.

Firebase is entirely opt-in.  The service-account file is named by the
``fcm_service_account_file`` setting, so a normal self-hosted installation
stores no credentials and simply does not start mobile delivery.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from . import config, ttsoutbox

FRESH_S = 300
RECENT = 30
RETRY_S = 30
_lock = threading.Lock()
_session = None


def state_path(tts: Path) -> Path:
    raw = config.get("tts_app_state")
    return Path(str(raw)).expanduser() if raw else Path(tts) / "app.json"


def account_path() -> Path | None:
    raw = config.get("fcm_service_account_file")
    return Path(str(raw)).expanduser() if raw else None


def configured() -> bool:
    path = account_path()
    return bool(path and path.is_file())


def load(tts: Path) -> dict:
    try:
        value = json.loads(state_path(tts).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(tts: Path, value: dict) -> None:
    path = state_path(tts)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(value, handle)
    os.replace(temporary, path)


def alive(value: dict, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    if not value.get("token"):
        return False
    pushed, taken = float(value.get("last_push") or 0), float(value.get("last_take") or 0)
    return pushed <= taken or now - pushed < ttsoutbox.PUSH_GRACE_S + 10


def status(tts: Path, now: float | None = None) -> dict:
    value = load(tts)
    return {"registered": bool(value.get("token")), "alive": alive(value, now), "since": value.get("at"),
            "notify": bool(value.get("token") and value.get("notify")), "last_push": value.get("last_push"),
            "last_take": value.get("last_take"), "error": value.get("error") or "",
            "configured": configured()}


def register(tts: Path, token: str, now: float | None = None, notify: bool = False, ring: bool = False) -> dict:
    token = str(token or "").strip()
    if not token or len(token) > 4096 or any(char.isspace() for char in token):
        raise ValueError("token")
    now = time.time() if now is None else now
    with _lock:
        value = load(tts)
        if value.get("token") != token:
            value = {}
        value.update(token=token, at=now, notify=bool(notify), ring=bool(ring))
        value.pop("last_push", None)
        value.pop("retry_at", None)
        _save(tts, value)
    return status(tts, now)


def forget(tts: Path) -> None:
    try:
        state_path(tts).unlink()
    except OSError:
        pass


def took(tts: Path, now: float | None = None) -> None:
    with _lock:
        value = load(tts)
        if value.get("token"):
            value["last_take"] = time.time() if now is None else now
            _save(tts, value)


def ready(root: Path, now: float | None = None) -> list[int]:
    now, upper = time.time() if now is None else now, ttsoutbox.top(root)
    out = []
    for seq in range(max(1, upper - RECENT), upper + 1):
        meta, audio = root / f"{seq}.json", root / f"{seq}.opus"
        try:
            fresh = now - meta.stat().st_mtime <= FRESH_S
        except OSError:
            continue
        if fresh and audio.is_file() and not any((root / f"{seq}{suffix}").exists()
                                                 for suffix in (".done", ".taken", ".pushed")):
            out.append(seq)
    return out


def _authed():
    global _session
    if _session is None:
        account = account_path()
        if not account or not account.is_file():
            return None
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
        credentials = service_account.Credentials.from_service_account_file(
            str(account), scopes=["https://www.googleapis.com/auth/firebase.messaging"])
        _session = (AuthorizedSession(credentials), credentials.project_id)
    return _session


def fcm_send(token: str, seq: int) -> str:
    """Send a data-only notification without sending clip text."""
    try:
        connection = _authed()
    except Exception as exc:
        return f"send: {type(exc).__name__}"
    if connection is None:
        return "not configured"
    session, project = connection
    message = {"message": {"token": token, "data": {"kind": "comms", "seq": str(int(seq))},
                           "android": {"priority": "HIGH", "ttl": f"{FRESH_S}s"}}}
    try:
        response = session.post(f"https://fcm.googleapis.com/v1/projects/{project}/messages:send", json=message, timeout=10)
    except Exception as exc:  # delivery must fall back to other players
        return f"send: {type(exc).__name__}"
    if response.status_code == 200:
        return "ok"
    try:
        error = response.json().get("error") or {}
        details = {str(item.get("errorCode")) for item in error.get("details") or [] if isinstance(item, dict)}
    except ValueError:
        details = set()
    return "gone" if response.status_code == 404 or "UNREGISTERED" in details else f"fcm {response.status_code}"


def push_pending(tts: Path, root: Path, send=fcm_send, now: float | None = None) -> bool:
    """Push one fresh clip.  Failure leaves it for web or phone players."""
    now, value = time.time() if now is None else now, load(tts)
    if not configured() or not alive(value, now) or now < float(value.get("retry_at") or 0):
        return False
    ttsoutbox.app_listening(root, now)
    sequences = ready(root, now)
    if not sequences:
        return False
    # Claim before sending.  Another player can be long-polling while the
    # network call is in flight, and must not take a clip the app is about to
    # receive.  A failed send removes every temporary claim below.
    for sequence in sequences:
        ttsoutbox.pushed(sequence, root, now)
    result = send(str(value["token"]), sequences[-1])
    if result == "ok":
        value["last_push"] = now
        value.pop("error", None)
        _save(tts, value)
        return True
    for sequence in sequences:
        (root / f"{sequence}.pushed").unlink(missing_ok=True)
    try:
        (root / ".app").unlink()
    except OSError:
        pass
    if result == "gone":
        forget(tts)
        return False
    value.update(error=result, retry_at=now + RETRY_S)
    _save(tts, value)
    return False
