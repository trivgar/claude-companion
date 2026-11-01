"""python -m agent_companion.mail draft | approve | voice-guide"""
from __future__ import annotations

import sys

from . import accounts, draft, queue, source_for, voice_guide


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    cmd, chosen = (args[0], args[1:]) if args else ("", [])
    configured = accounts()
    if len(chosen) > 1:
        print("usage: python -m agent_companion.mail draft|approve|voice-guide [account]", file=sys.stderr)
        return 2
    names = chosen or sorted(configured)
    if cmd in ("draft", "approve") and (not names or (chosen and chosen[0] not in configured)):
        print("failed: mail is not set up for this account", file=sys.stderr)
        return 1
    try:
        if cmd == "draft":
            print(f"queued {sum(draft.run(source_for(account)) for account in names)}")
        elif cmd == "approve":
            print(f"drafted {sum(queue.approve_pending(source_for(account)) for account in names)}")
        elif cmd == "voice-guide":
            print(f"wrote {voice_guide.build()}")
        else:
            print("usage: python -m agent_companion.mail draft|approve|voice-guide [account]", file=sys.stderr)
            return 2
    except Exception as exc:  # a failed run must not stop the timer
        print(f"failed: {exc}", file=sys.stderr)
        return 1
    return 0


sys.exit(main())
