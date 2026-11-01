"""Your live Claude Code sessions and this week's spend, in the terminal.

    python3 examples/sessions.py                 # reads ~/.claude
    python3 examples/sessions.py --home DIR      # another ~/.claude (e.g. the demo)
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent_companion.reader import SessionReader, order  # noqa: E402
from agent_companion.usage import UsageIndex  # noqa: E402

DOT = {"busy": "\033[33m●\033[0m", "idle": "\033[2m○\033[0m"}


def _ago(secs) -> str:
    if secs is None:
        return "-"
    secs = int(secs)
    return f"{secs}s" if secs < 60 else f"{secs // 60}m" if secs < 3600 else f"{secs // 3600}h"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--home", type=Path, default=Path.home() / ".claude")
    home = ap.parse_args().home
    rows = order(SessionReader(home).snapshot())
    print(f"\033[1m{'':2}{'SESSION':<14}{'MODEL':<12}{'CONTEXT':>8}{'COST':>9}{'LAST':>6}  SAID\033[0m")
    for r in rows:
        model = (r.get("model") or "").replace("claude-", "").replace("-5-5", " 5.5")
        ctx = f"{r['context_pct']:.0f}%" if r.get("context_pct") is not None else "-"
        said = r.get("last_reply") or r.get("title") or ""
        said = said if len(said) <= 50 else said[:49].rstrip() + "…"
        print(f"{DOT.get(r['status'], ' ')} {r['name'] or r['project']:<14}{model:<12}{ctx:>8}"
              f"{'$' + format(r['cost_usd'], '.2f'):>9}{_ago(r.get('last_activity')):>6}  {said}")
    with tempfile.TemporaryDirectory() as tmp:
        usage = UsageIndex(home, Path(tmp) / "cache.json")
        usage.refresh()
        snap = usage.snapshot()
    for key, label in (("five_hour", "last 5 hours"), ("seven_day", "last 7 days")):
        w = snap.get(key) or {}
        print(f"\n{label:>12}: ${w.get('cost_usd', 0):,.2f}  ({w.get('total_tokens', 0):,} tokens)", end="")
    print(f"\n\n{len(rows)} live, {sum(r['status'] == 'busy' for r in rows)} working")
    return 0


if __name__ == "__main__":
    sys.exit(main())
