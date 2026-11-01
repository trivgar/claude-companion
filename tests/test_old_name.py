"""The rename from claude-companion (2026-11-01) breaks nothing already running."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _py(code, **env):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("AGENT_COMPANION_", "CLAUDE_COMPANION_"))}
    e.update(env, PYTHONPATH=str(ROOT))
    return subprocess.run([sys.executable, "-c", code], env=e, capture_output=True, text=True, check=True).stdout.strip()


def test_the_old_import_name_is_the_same_module():
    assert _py("import claude_companion.reader as a, agent_companion.reader as b; print(a is b)") == "True"


def test_an_old_env_name_still_counts_and_the_new_one_wins():
    code = "import agent_companion, os; print(os.environ['AGENT_COMPANION_JOB'])"
    assert _py(code, CLAUDE_COMPANION_JOB="station") == "station"
    assert _py(code, CLAUDE_COMPANION_JOB="old", AGENT_COMPANION_JOB="new") == "new"


def test_a_phone_built_before_the_rename_still_sends_a_heard_key():
    from types import SimpleNamespace
    from agent_companion.server import auth
    old = SimpleNamespace(headers={"x-claude-companion-key": "k1", "x-claude-companion-token": "t1"})
    new = SimpleNamespace(headers={"x-agent-companion-key": "k2", "x-agent-companion-token": "t2"})
    assert (auth._owner_presented(old), auth._hook_presented(old)) == ("k1", "t1")
    assert (auth._owner_presented(new), auth._hook_presented(new)) == ("k2", "t2")
