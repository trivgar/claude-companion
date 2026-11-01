import json
import os
import stat
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.routing import APIRoute  # noqa: E402

from agent_companion import restore  # noqa: E402
from agent_companion.liveness import proc_start  # noqa: E402
from agent_companion import server  # noqa: E402
from agent_companion.server import auth  # noqa: E402
from agent_companion.server import restore as restore_routes  # noqa: E402
from routes_walk import all_routes  # noqa: E402

TOKEN = "t" * 32
OWNER = "o" * 64


def _registry(home: Path, name: str, session_id: str, cwd: Path, pane: int) -> None:
    sessions = home / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    sessions.joinpath(f"{name}.json").write_text(json.dumps({
        "pid": os.getpid(), "procStart": proc_start(os.getpid()), "sessionId": session_id,
        "cwd": str(cwd), "tmux": f"{name}:@1.%{pane}", "status": "idle",
    }))


@pytest.fixture
def fake_tools(tmp_path, monkeypatch):
    tools = tmp_path / "tools"
    tools.mkdir()
    state = tmp_path / "tmux-sessions"
    log = tmp_path / "tmux-log"
    tmux = tools / "tmux"
    tmux.write_text("""#!/bin/sh
if [ \"$1\" = \"list-sessions\" ]; then
  if [ -s \"$FAKE_TMUX_STATE\" ]; then cat \"$FAKE_TMUX_STATE\"; exit 0; fi
  echo \"no server running\" >&2; exit 1
fi
if [ \"$1\" = \"new-session\" ]; then
  printf '%s\\n' \"$*\" >> \"$FAKE_TMUX_LOG\"
  while [ \"$#\" -gt 0 ]; do
    if [ \"$1\" = \"-s\" ]; then echo \"$2\" >> \"$FAKE_TMUX_STATE\"; break; fi
    shift
  done
  exit 0
fi
exit 2
""")
    claude = tools / "claude"
    claude.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$FAKE_CLAUDE_LOG\"\n")
    for path in (tmux, claude):
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FAKE_TMUX_STATE", str(state))
    monkeypatch.setenv("FAKE_TMUX_LOG", str(log))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "claude-log"))
    return state, log


def test_snapshot_is_atomic_private_and_keeps_a_recent_multi_session_crash_copy(tmp_path, monkeypatch):
    home, data = tmp_path / "claude", tmp_path / "data"
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir(); second.mkdir()
    _registry(home, "first", "sid-first", first, 1)
    _registry(home, "second", "sid-second", second, 2)
    monkeypatch.setattr(restore, "_model_rows", lambda h: {
        (os.getpid(), "sid-first"): {"model": "model-a", "child": False},
        (os.getpid(), "sid-second"): {"model": "model-b", "child": False},
    })

    report = restore.snapshot(home, data, now=1000)
    saved = json.loads((data / restore.SNAPSHOT_NAME).read_text())
    assert report["sessions"] == 2 and report["crash_saved"] is False
    assert saved["sessions"] == [
        {"tmux": "first", "cwd": str(first), "session_id": "sid-first", "model": "model-a"},
        {"tmux": "second", "cwd": str(second), "session_id": "sid-second", "model": "model-b"},
    ]
    assert stat.S_IMODE((data / restore.SNAPSHOT_NAME).stat().st_mode) == 0o600

    for path in (home / "sessions").glob("*.json"):
        path.unlink()
    report = restore.snapshot(home, data, now=1001)
    assert report["sessions"] == 0 and report["crash_saved"] is True
    assert json.loads((data / restore.CRASH_SNAPSHOT_NAME).read_text())["sessions"] == saved["sessions"]


def test_a_crash_is_kept_though_a_new_session_started_first_and_not_while_one_survives(tmp_path, monkeypatch):
    home, data = tmp_path / "claude", tmp_path / "data"
    for name in ("one", "two", "three"):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(restore, "_model_rows", lambda h: {})
    _registry(home, "one", "sid-one", tmp_path / "one", 1)
    _registry(home, "two", "sid-two", tmp_path / "two", 2)
    restore.snapshot(home, data, now=1000)

    for path in (home / "sessions").glob("*.json"):
        path.unlink()
    _registry(home, "three", "sid-three", tmp_path / "three", 3)
    assert restore.snapshot(home, data, now=1001)["crash_saved"] is True

    (data / restore.CRASH_SNAPSHOT_NAME).unlink()
    _registry(home, "one", "sid-one", tmp_path / "one", 1)
    restore.snapshot(home, data, now=1002)
    for path in (home / "sessions").glob("*.json"):
        if "sid-three" in path.read_text():
            path.unlink()
    assert restore.snapshot(home, data, now=1003)["crash_saved"] is False


def test_the_timer_fires_at_boot_and_on_enable():
    text = restore._timer_text(5)
    assert "OnBootSec=" in text and "OnActiveSec=" in text and "OnUnitActiveSec=5min" in text


def test_status_and_restore_use_fake_tmux_and_claude_on_path(tmp_path, fake_tools):
    state, log = fake_tools
    data = tmp_path / "data"
    live, missing, gone = tmp_path / "live", tmp_path / "missing", tmp_path / "gone"
    live.mkdir(); missing.mkdir()
    state.write_text("live\n")
    restore._atomic_json(data / restore.SNAPSHOT_NAME, {"version": 1, "taken_at": 1000, "sessions": [
        {"tmux": "live", "cwd": str(live), "session_id": "sid-live", "model": "model-a"},
        {"tmux": "missing", "cwd": str(missing), "session_id": "sid-missing", "model": "model-b"},
        {"tmux": "gone", "cwd": str(gone), "session_id": "sid-gone", "model": ""},
    ]})

    status = restore.status(data, now=1001)
    assert status["ok"] is True and status["missing"] == 1
    assert [(row["tmux"], row["running"], row["gone"]) for row in status["sessions"]] == [
        ("gone", False, True), ("live", True, False), ("missing", False, False)]

    result = restore.restore(data, now=1001, which=lambda name: None)
    by_name = {row["tmux"]: row for row in result["sessions"]}
    assert result["ok"] is True and result["restored"] == 1
    assert by_name["live"]["detail"] == "already running"
    assert by_name["gone"]["detail"] == "working directory is gone"
    assert by_name["missing"] == {"tmux": "missing", "ok": True, "skipped": False, "detail": "started"}
    command = log.read_text()
    assert "-s missing" in command and "claude --resume sid-missing" in command
    assert "live" not in command


def test_crash_snapshot_is_offered_only_for_its_short_grace_period(tmp_path, fake_tools):
    data = tmp_path / "data"
    cwd = tmp_path / "work"
    cwd.mkdir()
    crash = {"version": 1, "taken_at": 1000, "sessions": [
        {"tmux": "saved", "cwd": str(cwd), "session_id": "sid", "model": ""},
    ]}
    restore._atomic_json(data / restore.SNAPSHOT_NAME, {"version": 1, "taken_at": 1001, "sessions": []})
    restore._atomic_json(data / restore.CRASH_SNAPSHOT_NAME, crash)
    assert restore.status(data, now=1002)["missing"] == 1
    assert restore.status(data, now=1000 + restore.CRASH_GRACE_S + 1)["ok"] is True
    assert restore.status(data, now=1000 + restore.CRASH_GRACE_S + 1)["missing"] == 0


def test_timer_installer_writes_only_its_two_units_and_uninstalls_them(tmp_path):
    calls = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def run(args, **kwargs):
        calls.append(args)
        return Result()

    units = tmp_path / "systemd"
    result = restore.install_timer(units, python="/opt/venv/bin/python3", interval_min=7, run=run)
    assert result == {"ok": True, "directory": str(units), "interval_min": 7}
    service = (units / restore.SERVICE_NAME).read_text()
    timer = (units / restore.TIMER_NAME).read_text()
    assert "-m agent_companion.restore snapshot" in service and "OnUnitActiveSec=7min" in timer
    assert calls[-1] == ["systemctl", "--user", "enable", "--now", restore.TIMER_NAME]

    result = restore.uninstall_timer(units, run=run)
    assert result["ok"] is True and not (units / restore.SERVICE_NAME).exists() and not (units / restore.TIMER_NAME).exists()
    assert ["systemctl", "--user", "disable", "--now", restore.TIMER_NAME] in calls


def test_restore_routes_use_the_owner_read_and_write_guards(monkeypatch):
    monkeypatch.setattr(server, "FEATURES", ["sessions"])
    monkeypatch.setattr(restore_routes.core, "status", lambda: {"ok": True, "missing": 1, "sessions": []})
    called = []
    monkeypatch.setattr(restore_routes.core, "restore", lambda: called.append(True) or {"ok": True, "sessions": []})
    app = server.create_app(token=TOKEN, owner_key=OWNER)
    routes = [route for route in all_routes(app) if isinstance(route, APIRoute) and route.path == "/api/restore"]
    read = next(route for route in routes if "GET" in route.methods)
    write = next(route for route in routes if "POST" in route.methods)
    assert read.dependant.dependencies[0].call is auth.require_token
    assert write.dependant.dependencies[0].call is auth.require_write
    assert read.endpoint()["missing"] == 1
    assert write.endpoint()["ok"] is True
    assert called == [True]


def test_sessions_view_lists_missing_snapshots_and_posts_the_restore():
    source = (Path(__file__).resolve().parents[1] / "agent_companion" / "server" / "static" / "sessions.js").read_text()
    assert "Restore ${missing.length} sessions" in source
    assert "CC.get('/api/restore')" in source and "CC.post('/api/restore', {})" in source
    assert "esc(r.tmux)" in source
