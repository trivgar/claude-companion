"""Notes between two people's Claudes, carried by GitHub Issues.

Code goes by pull request. A note goes by issue, labelled for whose Claude it
is for. `collab_label` (config) is YOUR label; empty means the feature is off.
Every helper fails quiet: a hook calling this must never slow or break a start.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from agent_companion import config

HOOK = Path(__file__).resolve().parent / "hooks" / "collab_hook.py"


def label() -> str:
    return str(config.get("collab_label", "") or "").strip()


def trusted() -> set[str]:
    """GitHub logins whose notes are shown (collab_from, comma separated).
    Empty means none are: a stranger's issue never reaches a session."""
    v = config.get("collab_from") or ""
    if isinstance(v, (list, tuple)):
        v = ",".join(map(str, v))
    return {x.strip().lower() for x in str(v).split(",") if x.strip()}


def _clean(text, cap: int) -> str:
    """One line, no control characters, at most cap characters."""
    s = "".join(c if c.isprintable() else " " for c in str(text or ""))
    s = " ".join(s.split())
    return s if len(s) <= cap else s[:cap - 3] + "..."


def _slug(url: str) -> str:
    m = re.search(r"github\.com[:/]+([^/\s]+/[^/\s]+?)(?:\.git)?/?$", url.strip())
    return m.group(1) if m else ""


def repo() -> str:
    r = str(config.get("collab_repo", "") or "").strip()
    if r:
        return r
    try:
        out = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent), "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    return _slug(out)


def open_for(label: str, repo: str, gh: str = "gh", timeout: float = 3.0) -> list[dict]:
    try:
        r = subprocess.run([gh, "issue", "list", "--repo", repo, "--label", label, "--state", "open",
                            "--json", "number,title,author,updatedAt,url", "--limit", "20"],
                           capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            return []
        data = json.loads(r.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    return [i for i in data if isinstance(i, dict)] if isinstance(data, list) else []


def file(label: str, title: str, body: str, repo: str, gh: str = "gh", timeout: float = 20.0) -> bool:
    try:
        # --force makes this a no-op when the label already exists.
        subprocess.run([gh, "label", "create", label, "--repo", repo, "--force"],
                       capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        r = subprocess.run([gh, "issue", "create", "--repo", repo, "--label", label,
                            "--title", title, "--body", body],
                           capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def _settings() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _groups(data: dict) -> list:
    return data.setdefault("hooks", {}).setdefault("SessionStart", [])


def _is_ours(h: dict) -> bool:
    return "collab_hook" in str(h.get("command", ""))


def _edit_settings(add: bool) -> str:
    p = _settings()
    try:
        data = json.loads(p.read_text()) if p.exists() else {}
    except (OSError, ValueError):
        return "settings.json unreadable; left alone"
    before = json.dumps(data, sort_keys=True)
    if add:
        if not any(_is_ours(h) for g in _groups(data) for h in g.get("hooks", [])):
            _groups(data).append({"hooks": [{"type": "command", "timeout": 5,
                                             "command": f"{sys.executable} {HOOK}"}]})
    else:
        groups = data.get("hooks", {}).get("SessionStart", [])
        for g in groups:
            g["hooks"] = [h for h in g.get("hooks", []) if not _is_ours(h)]
        if groups:
            data["hooks"]["SessionStart"] = [g for g in groups if g.get("hooks")]
            if not data["hooks"]["SessionStart"]:
                del data["hooks"]["SessionStart"]
    if json.dumps(data, sort_keys=True) == before:
        return "already " + ("installed" if add else "removed")
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        shutil.copy2(p, p.with_name(p.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S")))
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(p)
    return "installed" if add else "removed"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agent_companion.collab")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    f = sub.add_parser("file")
    f.add_argument("--to", required=True)
    f.add_argument("--title", required=True)
    f.add_argument("--body-file", required=True)
    sub.add_parser("install")
    sub.add_parser("uninstall")
    a = ap.parse_args(argv)
    if a.cmd in ("install", "uninstall"):
        print(_edit_settings(a.cmd == "install"))
        return 0
    r = repo()
    if not r:
        print("no repo: set collab_repo or run inside a clone with an origin", file=sys.stderr)
        return 1
    if a.cmd == "check":
        mine = label()
        if not mine:
            print("collab_label is not set; the feature is off")
            return 0
        allowed = trusted()
        notes = [n for n in open_for(mine, r, timeout=15)
                 if str((n.get("author") or {}).get("login", "")).lower() in allowed]
        print(f"{len(notes)} open note(s) for {mine} on {r}")
        for n in notes:
            print(f"#{n.get('number')} {n.get('title')} {n.get('url')}")
        return 0
    try:
        body = Path(a.body_file).read_text()
    except OSError as e:
        print(f"cannot read body: {e}", file=sys.stderr)
        return 1
    ok = file(a.to, a.title, body, r)
    print("filed" if ok else "could not file the issue (is gh logged in?)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
