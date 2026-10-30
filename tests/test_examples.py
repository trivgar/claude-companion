"""The README's examples run: the demo home builds, and sessions.py lists it."""
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_demo_home_lists_four_sessions(tmp_path):
    sys.path.insert(0, str(ROOT / "examples"))
    import demo_home
    home = demo_home.build(tmp_path)
    try:
        out = subprocess.run([sys.executable, str(ROOT / "examples" / "sessions.py"), "--home", str(home)],
                             capture_output=True, text=True, timeout=60).stdout
        assert "4 live, 2 working" in out, out
        assert "web-checkout" in out and "$" in out
    finally:
        for f in (home / "sessions").glob("*.json"):
            try:
                os.kill(json.loads(f.read_text())["pid"], signal.SIGTERM)
            except (OSError, ValueError, KeyError):
                pass
