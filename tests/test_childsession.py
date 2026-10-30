"""Labelling sessions Claude Code spawned, rather than ones you started.

They register as ordinary `kind: interactive` sessions, so `my-project-8f`
appeared between two agents with a cost and a context bar of its own, reading
as one of yours.
"""
from __future__ import annotations

import pytest

from claude_companion.reader import is_child_session

REAL = {"name": "garden", "name_source": "user", "bridge_session_id": "s_1",
        "tmux": "claude-garden:@3.%3"}
CHILD = {"name": "my-project-8f", "name_source": "auto",
         "bridge_session_id": "", "tmux": "claude-session:@0.%0"}


def test_a_session_the_owner_named_is_not_a_child():
    assert is_child_session(REAL, {"claude-garden:@3.%3": 1}) is False


def test_a_generated_name_is_a_child():
    assert is_child_session(CHILD, {"claude-session:@0.%0": 2}) is True


@pytest.mark.parametrize("src", ["auto", "generated", "spawn", "something-new"])
def test_any_non_user_source_counts(src):
    """Read as "present and not user" rather than compared against a list, so a
    new spawn kind labels correctly without anyone updating this code."""
    assert is_child_session({**CHILD, "name_source": src}, {}) is True


def test_a_user_named_session_sharing_a_pane_is_still_not_a_child():
    """nameSource is authoritative when present. Two real sessions can share a
    pane -- that is why the pane check is only ever the fallback."""
    s = {**REAL, "bridge_session_id": "", "tmux": "shared:@1.%1"}
    assert is_child_session(s, {"shared:@1.%1": 2}) is False


# --- the fallback, for a Claude Code with no nameSource ----------------------
# Measured twice on this machine on 2026-09-16: a child has no bridge of its
# own AND shares its parent's pane. The pair matters together.

def test_without_namesource_a_bridgeless_pane_sharer_is_a_child():
    s = {"name": "x", "bridge_session_id": "", "tmux": "p:@0.%0"}
    assert is_child_session(s, {"p:@0.%0": 2}) is True


def test_without_namesource_having_a_bridge_means_real():
    s = {"name": "x", "bridge_session_id": "s_9", "tmux": "p:@0.%0"}
    assert is_child_session(s, {"p:@0.%0": 2}) is False


def test_without_namesource_a_lone_session_is_real_even_with_no_bridge():
    """A real session can lose its bridge. Alone in its pane, it is not a child
    -- treating it as one would label a session of yours as a spawn."""
    s = {"name": "x", "bridge_session_id": "", "tmux": "p:@0.%0"}
    assert is_child_session(s, {"p:@0.%0": 1}) is False


def test_no_pane_and_no_bridge_is_not_a_child():
    assert is_child_session({"name": "x", "bridge_session_id": "", "tmux": ""}, {}) is False


def test_an_empty_record_does_not_explode():
    assert is_child_session({}, {}) is False


# --- the process tree, which decides when /proc can answer --------------------

from claude_companion.reader import spawned_by_session  # noqa: E402

# An SDK host's session as the registry records it: an SDK session
# with a name Claude Code derived, no bridge, no pane.
SDK_HOST = {"name": "agent-a6", "name_source": "derived", "entrypoint": "sdk-cli",
            "bridge_session_id": "", "tmux": ""}


def test_a_derived_name_under_an_sdk_host_is_not_a_child():
    """An SDK host's agent, whose parent process is the host, was labelled a
    subagent. Its name is derived because the SDK passed none, which says who
    named it and nothing about who started it."""
    tree = {500: 400, 400: 1}                  # agent -> sdk host -> init
    spawned = spawned_by_session(500, {500, 1392}, tree.get)
    assert spawned is False
    assert is_child_session(SDK_HOST, {}, spawned) is False


def test_a_session_under_another_sessions_process_is_a_child():
    """What a spawn is: `my-project-8f` shared its parent's pane
    because it ran under its parent's process."""
    tree = {700: 650, 650: 1392, 1392: 900, 900: 1}   # child -> shell -> claude
    spawned = spawned_by_session(700, {700, 1392}, tree.get)
    assert spawned is True
    assert is_child_session({**REAL, "name_source": "user"}, {}, spawned) is True, \
        "the tree decides even over a name you chose"


def test_an_unreadable_process_falls_back_to_the_old_rules():
    assert spawned_by_session(123, {123}, {}.get) is None
    assert is_child_session(CHILD, {}, None) is True
    assert is_child_session(REAL, {}, None) is False


def test_a_loop_in_proc_does_not_hang():
    assert spawned_by_session(5, {5, 99}, {5: 6, 6: 7, 7: 6}.get) is False


def test_the_reader_asks_the_tree(tmp_path):
    """End to end through SessionReader: two live sessions, one a derived
    SDK name whose parent is not a session. Not labelled."""
    import json, os
    from claude_companion.liveness import proc_start
    from claude_companion.reader import SessionReader
    me, parent = os.getpid(), os.getppid()
    (tmp_path / "sessions").mkdir()
    (tmp_path / "projects").mkdir()
    for pid, name, src in ((me, "agent-a6", "derived"), (parent, "garden", "user")):
        (tmp_path / "sessions" / f"{pid}.json").write_text(json.dumps({
            "pid": pid, "procStart": proc_start(pid), "sessionId": f"s{pid}",
            "cwd": "/home/user/agent", "kind": "interactive", "name": name,
            "nameSource": src, "entrypoint": "sdk-cli" if src == "derived" else "cli"}))
    tree = {me: 400, 400: 1, parent: 1}
    # The environment is faked as the tree is. These pids are the test
    # runner's own, and the default reader would read their real
    # /proc/<pid>/environ: run from inside a job (CLAUDE_TTS_SILENT or
    # CLAUDE_COMPANION_JOB set at exec), both rows would read as jobs.
    no_env = lambda pid, keys: {}
    rows = {r["name"]: r for r in SessionReader(tmp_path, ppid_of=tree.get, environ_of=no_env).snapshot()}
    assert rows["agent-a6"]["child"] is False
    assert rows["garden"]["child"] is False
    tree[me] = parent                              # now it runs under garden
    rows = {r["name"]: r for r in SessionReader(tmp_path, ppid_of=tree.get, environ_of=no_env).snapshot()}
    assert rows["agent-a6"]["child"] is True
