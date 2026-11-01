import os

from agent_companion.liveness import is_alive, proc_start


def test_proc_start_reads_own_process():
    assert proc_start(os.getpid()) is not None


def test_proc_start_missing_pid_returns_none():
    assert proc_start(2_147_483_600) is None


def test_is_alive_true_for_self():
    me = os.getpid()
    assert is_alive(me, proc_start(me)) is True


def test_is_alive_false_when_start_time_differs():
    """A recycled pid must not be treated as the original process."""
    assert is_alive(os.getpid(), "999999999") is False


def test_is_alive_false_for_dead_pid():
    assert is_alive(2_147_483_600, "12345") is False


def test_comm_with_spaces_does_not_shift_fields(tmp_path, monkeypatch):
    """Regression: `comm` can contain spaces and parens.

    /proc/<pid>/stat is "pid (comm) state ppid ...". starttime is field 22
    overall, so after stripping through the last ')' it is the 20th remaining
    field -- index 19, where index 0 is `state`.

    Here the fields after the state are the literal numbers 1..40, so the
    remainder is ["S", "1", "2", ...] and index 19 holds "19". A naive
    split of the whole line would take index 21 and read "18" instead.
    """
    fake = tmp_path / "stat"
    fields = " ".join(str(i) for i in range(1, 41))
    fake.write_text(f"4242 (npm exec agentv) S {fields}\n")
    monkeypatch.setattr("agent_companion.liveness.STAT_PATH", lambda pid: str(fake))

    assert proc_start(4242) == "19"
    # What the bug looked like: positional field 22 of the raw line.
    assert fake.read_text().split()[21] == "17"
