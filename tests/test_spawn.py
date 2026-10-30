"""Which session started each helper session, and which sessions are jobs.

A helper started under a session's process names that session as its parent.
A job started detached (setsid) has no session above it, so the process tree
cannot tell it from a person's session; its environment says so instead.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from claude_companion.liveness import proc_environ, proc_start
from claude_companion.reader import (SPAWN_DEPTH, SessionReader, job_of, root_of,
                                     spawned_by_session, spawning_session)


# --- spawning_session: the walk up the process tree ---------------------------

def test_the_nearest_session_above_is_named():
    tree = {700: 650, 650: 300, 300: 200, 200: 1}     # child -> shell -> session -> session
    assert spawning_session(700, {700, 300, 200}, tree.get) == 300


def test_reaching_init_is_zero():
    assert spawning_session(500, {500, 1392}, {500: 400, 400: 1}.get) == 0


def test_an_unreadable_process_is_none():
    assert spawning_session(123, {123}, {}.get) is None


def test_a_loop_is_zero_and_does_not_hang():
    assert spawning_session(5, {5, 99}, {5: 6, 6: 7, 7: 6}.get) == 0


def test_a_tree_deeper_than_the_bound_is_zero():
    tree = {n: n + 1 for n in range(10, 10 + SPAWN_DEPTH + 5)}
    tree[10 + SPAWN_DEPTH + 5] = 999
    assert spawning_session(10, {10, 999}, tree.get) == 0


def test_a_process_gone_mid_walk_is_zero():
    """Its parent answered, the next one up did not: no session found."""
    assert spawning_session(5, {5, 99}, {5: 6}.get) == 0


@pytest.mark.parametrize("tree,want", [
    ({700: 650, 650: 1392}, True), ({500: 400, 400: 1}, False), ({}, None)])
def test_spawned_by_session_is_the_same_answer_as_a_bool(tree, want):
    pid = next(iter(tree), 9)
    assert spawned_by_session(pid, {pid, 1392}, tree.get) is want


# --- job_of: the environment's say ---------------------------------------------

def test_a_named_job_and_whose_work_it_is_about():
    assert job_of({"CLAUDE_COMPANION_JOB": "summary", "CLAUDE_COMPANION_ABOUT": "Ada"}) \
        == ("summary", "Ada")


def test_a_silenced_headless_run_is_a_job_too():
    assert job_of({"CLAUDE_TTS_SILENT": "1"}) == ("headless", "")


def test_the_job_name_wins_over_the_silence():
    assert job_of({"CLAUDE_TTS_SILENT": "1", "CLAUDE_COMPANION_JOB": "digest"}) == ("digest", "")


def test_no_marker_is_no_job():
    assert job_of({}) == ("", "")
    assert job_of({"CLAUDE_TTS_SILENT": "", "CLAUDE_COMPANION_JOB": "  "}) == ("", "")


def test_about_alone_does_not_make_a_job():
    assert job_of({"CLAUDE_COMPANION_ABOUT": "Ada"}) == ("", "")


def test_a_marker_is_cut_to_a_chip():
    job, about = job_of({"CLAUDE_COMPANION_JOB": "a\nb   " + "x" * 200, "CLAUDE_COMPANION_ABOUT": "\tz "})
    assert job.startswith("a b x") and len(job) == 64
    assert about == "z"


# --- root_of --------------------------------------------------------------------

def test_root_walks_through_children_to_the_first_session_that_is_not_one():
    rows = {1: {"child": False, "parent_pid": None},
            2: {"child": True, "parent_pid": 1},
            3: {"child": True, "parent_pid": 2}}
    assert [root_of(p, rows) for p in (1, 2, 3)] == [None, 1, 1]


def test_root_is_none_under_a_parentless_child_or_a_loop():
    rows = {1: {"child": True, "parent_pid": None, "job": "x"},
            2: {"child": True, "parent_pid": 1},
            3: {"child": True, "parent_pid": 4},
            4: {"child": True, "parent_pid": 3}}
    assert root_of(2, rows) is None
    assert root_of(3, rows) is None
    assert root_of(9, rows) is None


# --- proc_environ: only what was asked for --------------------------------------

def test_proc_environ_reads_only_the_named_keys_of_a_real_process():
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "CLAUDE_COMPANION_JOB": "digest", "SOME_SECRET_TOKEN": "hunter2"}
    # Popen returns as exec starts, before the kernel has set out the new
    # environment, and a read in that window is empty. So wait for the child
    # to say it is running.
    p = subprocess.Popen([sys.executable, "-c", "import time; print('up', flush=True); time.sleep(30)"],
                         env=env, stdout=subprocess.PIPE)
    try:
        assert p.stdout.readline().strip() == b"up"
        got = proc_environ(p.pid, ["CLAUDE_COMPANION_JOB", "CLAUDE_COMPANION_ABOUT"])
        assert got == {"CLAUDE_COMPANION_JOB": "digest"}
        assert proc_environ(p.pid, []) == {}
    finally:
        p.kill()
        p.wait()
        p.stdout.close()


def test_proc_environ_of_a_fake_file(tmp_path, monkeypatch):
    fake = tmp_path / "environ"
    fake.write_bytes(b"SECRET=s3cr3t\0A=1=2\0B\0A=second\0C=\xff\xfe\0NOVALUE=\0")
    monkeypatch.setattr("claude_companion.liveness.ENVIRON_PATH", lambda pid: str(fake))
    got = proc_environ(42, ["A", "B", "C", "NOVALUE", "MISSING"])
    assert got == {"A": "1=2", "C": "��", "NOVALUE": ""}
    assert "SECRET" not in got and "s3cr3t" not in json.dumps(got)


def test_proc_environ_of_a_missing_process_is_empty():
    assert proc_environ(2_147_483_600, ["PATH"]) == {}


# --- the reader, end to end ------------------------------------------------------

@pytest.fixture
def procs():
    """Live processes to register as sessions: the registry checks each pid
    against /proc, and the tree between them is drawn by the test."""
    ps = []

    def make(n):
        for _ in range(n):
            ps.append(subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]))
        return [p.pid for p in ps[-n:]]
    yield make
    for p in ps:
        p.kill()
        p.wait()


def _register(home, pid, name, *, src="user", entrypoint="cli", title="", prompt="", tmux=""):
    cwd = "/tmp/work"
    slug = cwd.replace("/", "-")
    (home / "sessions").mkdir(parents=True, exist_ok=True)
    (home / "projects" / slug).mkdir(parents=True, exist_ok=True)
    (home / "sessions" / f"{pid}.json").write_text(json.dumps({
        "pid": pid, "procStart": proc_start(pid), "sessionId": f"s{pid}", "cwd": cwd,
        "kind": "interactive", "name": name, "nameSource": src, "entrypoint": entrypoint,
        "status": "busy", "tmux": tmux}))
    lines = []
    if title:
        lines.append({"type": "ai-title", "aiTitle": title})
    if prompt:
        lines.append({"type": "user", "message": {"role": "user", "content": prompt}})
    (home / "projects" / slug / f"s{pid}.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in lines))


class FakeEnviron:
    """environ_of for a test: per-pid environments, and a record of each ask."""
    def __init__(self, envs):
        self.envs, self.asked = envs, []

    def __call__(self, pid, keys):
        self.asked.append(pid)
        return {k: v for k, v in self.envs.get(pid, {}).items() if k in keys}


def test_a_detached_job_is_a_child_with_no_parent(tmp_path, procs):
    """The detached run has no session above it; its environment names it."""
    agent, job = procs(2)
    _register(tmp_path, agent, "scratch", tmux="w:@1.%1")
    _register(tmp_path, job, "proj-ae", src="derived", entrypoint="sdk-cli",
              prompt="You are reading a briefing: summarise", tmux="w:@1.%1")
    env = FakeEnviron({job: {"CLAUDE_COMPANION_JOB": "station", "CLAUDE_COMPANION_ABOUT": "Wheatley"}})
    tree = {agent: 1, job: 900, 900: 1}
    rows = {r["pid"]: r for r in SessionReader(tmp_path, ppid_of=tree.get, environ_of=env).snapshot()}
    j = rows[job]
    assert (j["job"], j["job_about"], j["child"]) == ("station", "Wheatley", True)
    assert j["parent_pid"] is None and j["root_pid"] is None, \
        "a shared tmux pane never names a parent"
    assert j["label"] == "reading a briefing"
    a = rows[agent]
    assert (a["job"], a["job_about"], a["child"], a["label"]) == ("", "", False, "")
    assert a["parent_pid"] is None and a["root_pid"] is None


def test_a_headless_run_with_only_the_silence_marker(tmp_path, procs):
    (job,) = procs(1)
    _register(tmp_path, job, "proj-b1", src="derived", entrypoint="sdk-cli")
    env = FakeEnviron({job: {"CLAUDE_TTS_SILENT": "1"}})
    (row,) = SessionReader(tmp_path, ppid_of={job: 1}.get, environ_of=env).snapshot()
    assert (row["job"], row["child"], row["label"]) == ("headless", True, "subagent")


def test_a_helper_under_a_session_names_it(tmp_path, procs):
    top, helper = procs(2)
    _register(tmp_path, top, "salon")
    _register(tmp_path, helper, "salon-8f", src="derived", title="You are reviewing Task 1: the fix")
    tree = {top: 1, helper: 650, 650: top}             # helper -> its Bash tool's shell -> top
    env = FakeEnviron({})
    rows = {r["pid"]: r for r in SessionReader(tmp_path, ppid_of=tree.get, environ_of=env).snapshot()}
    h = rows[helper]
    assert (h["child"], h["parent_pid"], h["root_pid"]) == (True, top, top)
    assert h["label"] == "reviewing Task 1" and h["job"] == ""
    assert env.asked == [top], "the environment is asked only where no session is above"


def test_a_grandchild_names_its_parent_and_the_top(tmp_path, procs):
    top, mid, low = procs(3)
    _register(tmp_path, top, "salon")
    _register(tmp_path, mid, "salon-8f", src="derived", title="Fixing the booking page")
    _register(tmp_path, low, "salon-9c", src="derived", prompt="Run the tests")
    tree = {top: 1, mid: 650, 650: top, low: 660, 660: mid}
    rows = {r["pid"]: r for r in SessionReader(tmp_path, ppid_of=tree.get,
                                               environ_of=FakeEnviron({})).snapshot()}
    assert (rows[mid]["parent_pid"], rows[mid]["root_pid"]) == (top, top)
    assert (rows[low]["parent_pid"], rows[low]["root_pid"]) == (mid, top)
    assert rows[low]["label"] == "Run the tests"
    assert (rows[top]["parent_pid"], rows[top]["root_pid"], rows[top]["child"]) == (None, None, False)


def test_a_helper_of_a_job_has_the_job_as_parent_and_no_root(tmp_path, procs):
    job, helper = procs(2)
    _register(tmp_path, job, "proj-ae", src="derived", entrypoint="sdk-cli")
    _register(tmp_path, helper, "proj-c2", src="derived")
    tree = {job: 1, helper: job}
    env = FakeEnviron({job: {"CLAUDE_COMPANION_JOB": "digest"}})
    rows = {r["pid"]: r for r in SessionReader(tmp_path, ppid_of=tree.get, environ_of=env).snapshot()}
    assert (rows[helper]["parent_pid"], rows[helper]["root_pid"], rows[helper]["job"]) == (job, None, "")


def test_an_sdk_session_with_no_marker_and_no_session_above_stays_a_peer(tmp_path, procs):
    """An SDK host's own agent: derived name, sdk-cli, no session
    ancestor and no marker. Not a child, not a job."""
    (sdk,) = procs(1)
    _register(tmp_path, sdk, "agent-a6", src="derived", entrypoint="sdk-cli", title="Main thread")
    (row,) = SessionReader(tmp_path, ppid_of={sdk: 400, 400: 1}.get,
                           environ_of=FakeEnviron({})).snapshot()
    assert (row["child"], row["job"], row["parent_pid"], row["root_pid"], row["label"]) == \
        (False, "", None, None, "")


def test_unreadable_proc_falls_back_and_still_asks_for_a_job(tmp_path, procs):
    (pid,) = procs(1)
    _register(tmp_path, pid, "auto-name", src="derived")
    env = FakeEnviron({})
    (row,) = SessionReader(tmp_path, ppid_of={}.get, environ_of=env).snapshot()
    assert row["child"] is True and row["parent_pid"] is None   # nameSource fallback
    assert env.asked == [pid]


def test_the_environment_is_read_once_per_process(tmp_path, procs):
    (pid,) = procs(1)
    _register(tmp_path, pid, "scratch")
    env = FakeEnviron({})
    reader = SessionReader(tmp_path, ppid_of={pid: 1}.get, environ_of=env)
    reader.snapshot()
    reader.snapshot()
    assert env.asked == [pid]


def test_only_the_job_keys_are_kept_whatever_environ_returns(tmp_path, procs):
    (pid,) = procs(1)
    _register(tmp_path, pid, "proj-ae", src="derived")
    reader = SessionReader(tmp_path, ppid_of={pid: 1}.get,
                           environ_of=lambda p, keys: {"GITHUB_TOKEN": "ghp_secret",
                                                       "CLAUDE_COMPANION_JOB": "digest"})
    (row,) = reader.snapshot()
    assert row["job"] == "digest"
    assert "ghp_secret" not in json.dumps(row)
    assert all("GITHUB_TOKEN" not in env for env in reader._job_env.values())


def test_a_failing_environ_reader_is_no_job(tmp_path, procs):
    (pid,) = procs(1)
    _register(tmp_path, pid, "scratch")

    def boom(p, keys):
        raise PermissionError(p)
    (row,) = SessionReader(tmp_path, ppid_of={pid: 1}.get, environ_of=boom).snapshot()
    assert (row["job"], row["child"]) == ("", False)
