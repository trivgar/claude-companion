"""Put the companion hooks into Claude Code's settings.

    python -m agent_companion.server.install_hooks [--uninstall] [--settings PATH]

Adds the asks hooks, deferred-handoff hooks, and the commit-recording PostToolUse hook to
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

ASK_MODULE = "agent_companion.hooks.permission_hook"
COMMIT_MODULE = "agent_companion.hooks.commit_hook"
DEFERRED_MODULE = "agent_companion.hooks.deferred_hook"
TIMEOUT_S = 86400                    # the hook waits as long as the ask is open
COMMIT_TIMEOUT_S = 5
DEFERRED_TIMEOUT_S = 5


def settings_path() -> Path:
    return Path(os.environ.get("CLAUDE_HOME") or Path.home() / ".claude") / "settings.json"


def _ours(entry, module: str) -> bool:
    return isinstance(entry, dict) and any(
        module in str(h.get("command", "")) for h in entry.get("hooks", []) if isinstance(h, dict))


def _wanted(python: str) -> dict[str, tuple[dict, str]]:
    ask = {"type": "command", "command": f"{python} -m {ASK_MODULE}", "timeout": TIMEOUT_S}
    commit = {"type": "command", "command": f"{python} -m {COMMIT_MODULE}", "timeout": COMMIT_TIMEOUT_S}
    deferred = {"type": "command", "command": f"{python} -m {DEFERRED_MODULE}", "timeout": DEFERRED_TIMEOUT_S}
    return {"PermissionRequest": ({"matcher": ".*", "hooks": [dict(ask)]}, ASK_MODULE),
            "PreToolUse": ({"matcher": "AskUserQuestion", "hooks": [dict(ask)]}, ASK_MODULE),
            "PostToolUse": ({"matcher": "Bash", "hooks": [dict(commit)]}, COMMIT_MODULE),
            "SessionStart": ({"matcher": ".*", "hooks": [dict(deferred)]}, DEFERRED_MODULE),
            "UserPromptSubmit": ({"matcher": ".*", "hooks": [dict(deferred)]}, DEFERRED_MODULE)}


def apply(settings: dict, python: str = sys.executable, uninstall: bool = False) -> dict:
    """The settings with our hooks added (or removed). Pure."""
    out = json.loads(json.dumps(settings))
    hooks = out.setdefault("hooks", {})
    for event, (entry, module) in _wanted(python).items():
        kept = [e for e in hooks.get(event, []) if not _ours(e, module)]
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
    return ("removed the companion hooks from " if uninstall else "installed the companion hooks in ") + str(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--settings", type=Path, default=None)
    args = ap.parse_args(argv)
    print(run(args.settings or settings_path(), args.uninstall))
    return 0


if __name__ == "__main__":
    sys.exit(main())
