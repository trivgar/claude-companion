"""A made-up ~/.claude with four live sessions, for trying the library without
your own (and for the README's screenshots).

    python3 examples/demo_home.py /tmp/demo        # builds /tmp/demo/.claude
    python3 examples/sessions.py --home /tmp/demo/.claude

Each session needs a live process, so this starts one `sleep` per session; they
exit on their own after an hour (or `pkill -f '^sleep 3601$'`).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from claude_companion.liveness import proc_start  # noqa: E402

SESSIONS = [
    # project, name, status, model, title, last prompt, last reply, minutes ago, turns, final context
    ("api", "api-1", "busy", "claude-opus-5-5", "Rate limiter for the public API",
     "Add a per-key rate limiter and cover it with tests",
     "Token bucket in place; 14 new tests passing. Wiring it into the router now.", 0.2, 90, 182_000),
    ("web", "web-checkout", "idle", "claude-sonnet-5-5", "Checkout form validation",
     "Why does the checkout form accept an empty postcode?",
     "Fixed: the schema marked postcode optional. Pushed to fix/postcode.", 12, 40, 64_000),
    ("docs", "docs", "idle", "claude-haiku-5-5", "Changelog for 2.4",
     "Draft the changelog for 2.4 from the merged PRs",
     "Draft is in CHANGELOG.md, 23 entries, grouped by area.", 45, 18, 31_000),
    ("infra", "infra", "busy", "claude-opus-5-5", "Migrate CI to the new runners",
     "Move CI to the arm64 runners and keep the cache",
     "Two jobs green on arm64; the e2e job needs a bigger runner.", 0.5, 120, 141_000),
]


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def build(root: Path) -> Path:
    home = root / ".claude"
    (home / "sessions").mkdir(parents=True, exist_ok=True)
    now = time.time()
    for i, (project, name, status, model, title, prompt, reply, ago, turns, ctx) in enumerate(SESSIONS):
        pid = subprocess.Popen(["sleep", "3601"], start_new_session=True, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).pid
        cwd = f"/home/you/code/{project}"
        sid = f"demo-{i}"
        (home / "sessions" / f"{pid}.json").write_text(json.dumps({
            "pid": pid, "sessionId": sid, "cwd": cwd, "procStart": proc_start(pid),
            "status": status, "name": name, "nameSource": "user", "kind": "interactive",
            "entrypoint": "cli", "startedAt": int((now - 7200) * 1000)}))
        folder = home / "projects" / cwd.replace("/", "-")
        folder.mkdir(parents=True, exist_ok=True)
        end = now - ago * 60
        lines = [{"type": "ai-title", "aiTitle": title}]
        # Turns spread over the last two hours, the context growing as it would.
        for n in range(turns):
            t = end - 7000 * (1 - n / turns)
            lines.append({"type": "assistant", "timestamp": _iso(t), "cwd": cwd, "sessionId": sid,
                          "message": {"id": f"msg-{i}-{n}", "model": model, "role": "assistant",
                                      "content": [{"type": "text", "text": "Working."}],
                                      "usage": {"input_tokens": 6, "output_tokens": 900,
                                                "cache_read_input_tokens": int(20_000 + (ctx - 24_000) * n / turns),
                                                "cache_creation_input_tokens": 4_000}}})
        lines.append({"type": "user", "timestamp": _iso(end - 40), "cwd": cwd, "sessionId": sid,
                      "message": {"role": "user", "content": prompt}})
        lines.append({"type": "assistant", "timestamp": _iso(end), "cwd": cwd, "sessionId": sid,
                      "message": {"id": f"msg-{i}-last", "model": model, "role": "assistant",
                                  "content": [{"type": "text", "text": reply}],
                                  "usage": {"input_tokens": 6, "output_tokens": 700,
                                            "cache_read_input_tokens": ctx - 4_000,
                                            "cache_creation_input_tokens": 4_000}}})
        f = folder / f"{sid}.jsonl"
        f.write_text("".join(json.dumps(x) + "\n" for x in lines))
        os.utime(f, (end, end))
    return home


if __name__ == "__main__":
    print(build(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/claude-companion-demo")))
