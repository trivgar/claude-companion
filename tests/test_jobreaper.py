import json
import signal

from claude_companion import bgjobs, jobreaper


def _proc(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "stat").write_text("btime 1000\n")

    def add(pid, ppid, started, command, comm="process"):
        folder = proc / str(pid)
        folder.mkdir()
        fields = ["S", str(ppid)] + ["0"] * 17 + [str(started)]
        (folder / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields) + "\n")
        (folder / "cmdline").write_bytes(b"\0".join(part.encode() for part in command) + b"\0")
        (folder / "comm").write_text(comm + "\n")

    return proc, add


def _shell(command="sleep 60"):
    return ["/bin/bash", "-c",
            "source /tmp/.claude/shell-snapshots/snapshot-bash-7.sh && eval '" + command + "'"]


def test_stuck_finds_only_old_snapshot_shells_without_a_claude_parent(tmp_path, monkeypatch):
    proc, add = _proc(tmp_path)
    add(201, 1, 100, _shell(), "bash")
    add(202, 201, 150, ["sleep", "60"])
    add(203, 1, 399900, _shell("sleep 1"), "bash")
    monkeypatch.setattr(bgjobs, "_clock_ticks", lambda: 100)

    rows = jobreaper.stuck(str(proc), now=5000, minimum_age_s=3600)

    assert rows == [{"pid": 201, "ppid": 1, "start_ticks": 100, "age_s": 3999,
                     "cmd": "sleep 60"}]


def test_reaper_never_signals_a_snapshot_shell_with_a_live_claude_owner(tmp_path, monkeypatch):
    proc, add = _proc(tmp_path)
    add(101, 1, 1, ["claude", "--resume"], "claude")
    add(201, 101, 100, _shell(), "bash")
    monkeypatch.setattr(bgjobs, "_clock_ticks", lambda: 100)
    sent = []

    rows = jobreaper.reap(str(proc), tmp_path / "data", now=5000, minimum_age_s=1,
                           signal_fn=lambda pid, sig: sent.append((pid, sig)), sleep_fn=lambda _: None)

    assert rows == []
    assert sent == []
    assert not (tmp_path / "data" / jobreaper.LOG_NAME).exists()


def test_kill_does_not_enter_a_descendant_live_claude_session(tmp_path, monkeypatch):
    proc, add = _proc(tmp_path)
    add(201, 1, 100, _shell(), "bash")
    add(202, 201, 110, ["claude", "--resume"], "claude")
    add(203, 202, 120, _shell(), "bash")
    monkeypatch.setattr(bgjobs, "_clock_ticks", lambda: 100)
    row = jobreaper.stuck(str(proc), now=5000, minimum_age_s=1)[0]
    sent = []

    jobreaper.kill(row, str(proc), signal_fn=lambda pid, sig: sent.append((pid, sig)), sleep_fn=lambda _: None)

    assert sent == [(201, signal.SIGTERM), (201, signal.SIGKILL)]


def test_reaper_signals_the_orphan_tree_and_writes_jsonl(tmp_path, monkeypatch):
    proc, add = _proc(tmp_path)
    add(201, 1, 100, _shell("pytest -q"), "bash")
    add(202, 201, 150, ["sleep", "60"])
    monkeypatch.setattr(bgjobs, "_clock_ticks", lambda: 100)
    sent = []

    rows = jobreaper.reap(str(proc), tmp_path / "data", now=5000, minimum_age_s=3600,
                           signal_fn=lambda pid, sig: sent.append((pid, sig)), sleep_fn=lambda _: None)

    assert [row["pid"] for row in rows] == [201]
    assert sent == [(202, signal.SIGTERM), (201, signal.SIGTERM),
                    (202, signal.SIGKILL), (201, signal.SIGKILL)]
    record = json.loads((tmp_path / "data" / jobreaper.LOG_NAME).read_text())
    assert record == {"at": "1970-01-01T01:23:20+00:00", "pid": 201, "ppid": 1,
                      "age_s": 3999, "cmd": "pytest -q"}


def test_zero_interval_disables_the_worker(monkeypatch):
    monkeypatch.setattr(jobreaper.config, "get", lambda key, default=None: 0 if key == "job_reaper_every_min" else default)

    assert jobreaper.every_s() == 0
    assert jobreaper.start() is None
