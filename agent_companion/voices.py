"""Status of optional remote speech hosts.

Hosts come only from ``remote-hosts.conf`` in the configured coordinator
directory.  No host, address or local hardware name is built into the core.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from pathlib import Path

CACHE_S = 20
PROBE_S = 1.5
_cache: dict = {}
_flight = threading.Lock()


def hosts(tts: Path) -> list[tuple[str, str, set[str]]]:
    """Parse ``name=url [voice,...]`` rows, ignoring malformed rows."""
    try:
        lines = (Path(tts) / "remote-hosts.conf").read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        name, separator, rest = line.split("#", 1)[0].strip().partition("=")
        parts = rest.replace(",", " ").split()
        if not separator or not name or not parts or not parts[0].startswith(("http://", "https://")):
            continue
        voices = {"*"} if not out else {voice.rstrip("+") for voice in parts[1:] if voice.rstrip("+")}
        out.append((name[:80], parts[0], voices))
    return out


def own_versions(tts: Path) -> dict[str, str]:
    out = {}
    for number, (host, _url, selected) in enumerate(hosts(tts)):
        if number:
            try:
                line = (Path(tts) / "remote-hosts.conf").read_text().splitlines()[number]
                values = line.split("=", 1)[1].replace(",", " ").split()[1:]
                out.update({value[:-1]: host for value in values if value.endswith("+")})
            except (OSError, IndexError):
                pass
    return out


def wanted(tts: Path) -> set[str]:
    out = set()
    try:
        lines = (Path(tts) / "remote-voices.conf").read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            voice = line.split("=", 1)[1].strip()
            if voice:
                out.add(voice)
    return out


def _health(url: str, timeout: float = PROBE_S) -> dict | None:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=timeout) as response:
            body = json.loads(response.read().decode())
        return body if isinstance(body, dict) else None
    except (OSError, ValueError):
        return None


def _ask(items, health) -> dict[str, dict | None]:
    result: dict[str, dict | None] = {}
    threads = [threading.Thread(target=lambda name=name, url=url: result.update({name: health(url)}), daemon=True)
               for name, url, _voices in items]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + PROBE_S + 0.3
    for thread in threads:
        thread.join(max(0, deadline - time.monotonic()))
    return {name: result.get(name) for name, _url, _voices in items}


def _reason(host: str, report: dict | None) -> str:
    if report is None:
        return f"{host} is off or out of reach"
    state = report.get("state") or ("busy" if report.get("busy") else "ready")
    if state == "ready":
        return ""
    return str(report.get("why") or (f"{host} is busy" if state == "busy" else f"{host} is unavailable"))[:200]


def _compute(tts: Path, now: float, health) -> dict:
    selected, required = hosts(tts), wanted(tts)
    if not selected or not required:
        return {"real": True, "why": "", "note": "", "at": now, "configured": bool(selected)}
    applicable = [item for index, item in enumerate(selected) if index == 0 or "*" in item[2] or item[2] & required]
    reports = _ask(applicable, health)
    ready = [name for name, _url, _voices in applicable if not _reason(name, reports[name])]
    served = set()
    for name, _url, listed in applicable:
        if name in ready:
            served |= required if "*" in listed else listed & required
    reasons = "; ".join(_reason(name, reports[name]) for name, _url, _voices in applicable if _reason(name, reports[name]))
    if not ready or not required <= served:
        detail = reasons or "no configured host can serve these voices"
        if ready and served:
            detail += "; only " + ", ".join(sorted(served)) + " are available"
        return {"real": False, "why": detail, "note": "", "at": now, "configured": True}
    return {"real": True, "why": "", "note": f"Voices on {', '.join(ready)}" if len(ready) < len(applicable) else "",
            "at": now, "configured": True}


def state(tts: Path, now: float | None = None, health=_health) -> dict:
    now, key = time.time() if now is None else now, str(Path(tts))
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_S:
        return hit[1]
    if not _flight.acquire(blocking=False):
        return hit[1] if hit else _compute(Path(tts), now, health)
    try:
        value = _compute(Path(tts), now, health)
        _cache[key] = (now, value)
        return value
    finally:
        _flight.release()
