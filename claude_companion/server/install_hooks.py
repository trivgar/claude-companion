"""Put the ask hooks into Claude Code's settings.

    python -m claude_companion.server.install_hooks [--uninstall] [--settings PATH]

Adds the PermissionRequest hook and the AskUserQuestion PreToolUse hook to
~/.claude/settings.json (or $CLAUDE_HOME/settings.json) after a timestamped
backup. Safe to run twice: when nothing would change, nothing is written and
no backup is made. Other hooks are left exactly as they were.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

MODULE = "claude_companion.hooks.permission_hook"
TIMEOUT_S = 86400                    # the hook waits as long as the ask is open


def settings_path() -> Path:
    return Path(os.environ.get("CLAUDE_HOME") or Path.home() / ".claude") / "settings.json"


def _ours(entry) -> bool:
    return isinstance(entry, dict) and any(
        MODULE in str(h.get("command", "")) for h in entry.get("hooks", []) if isinstance(h, dict))


def _wanted(python: str) -> dict[str, dict]:
    hook = {"type": "command", "command": f"{python} -m {MODULE}", "timeout": TIMEOUT_S}
    return {"PermissionRequest": {"matcher": ".*", "hooks": [dict(hook)]},
            "PreToolUse": {"matcher": "AskUserQuestion", "hooks": [dict(hook)]}}


def apply(settings: dict, python: str = sys.executable, uninstall: bool = False) -> dict:
    """The settings with our hooks added (or removed). Pure."""
    out = json.loads(json.dumps(settings))
    hooks = out.setdefault("hooks", {})
    for event, entry in _wanted(python).items():
        kept = [e for e in hooks.get(event, []) if not _ours(e)]
        if not uninstall:
            kept.append(entry)
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    if not hooks:
        out.pop("hooks", None)
    return out


def run(path: Path, uninstall: bool = False, python: str = sys.executable) -> str:
    """Returns what happened, in words."""
    path = Path(path)
    try:
        before = json.loads(path.read_text())
    except FileNotFoundError:
        before = {}
    if not isinstance(before, dict):
        raise SystemExit(f"{path} is not a JSON object; not touching it")
    after = apply(before, python, uninstall)
    if after == before:
        return "already " + ("absent" if uninstall else "installed") + ": nothing changed"
    if path.exists():
        shutil.copy2(path, path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}"))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(after, indent=2) + "\n")
    os.replace(tmp, path)
    return ("removed the ask hooks from " if uninstall else "installed the ask hooks in ") + str(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--settings", type=Path, default=None)
    args = ap.parse_args(argv)
    print(run(args.settings or settings_path(), args.uninstall))
    return 0


if __name__ == "__main__":
    sys.exit(main())
