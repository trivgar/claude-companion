import multiprocessing

import pytest

from claude_companion import deferred


def _concurrent_add(path, cwd, number):
    deferred.add(f"item {number}", by="worker", path=path, cwd=cwd)


def test_concurrent_adds_are_all_kept_under_the_file_lock(tmp_path):
    path, cwd = tmp_path / "deferred.md", tmp_path / "project"
    cwd.mkdir()
    context = multiprocessing.get_context()
    workers = [context.Process(target=_concurrent_add, args=(path, cwd, number)) for number in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(20)
        assert worker.exitcode == 0
    entries = deferred.read(path)
    assert {entry["ask"] for entry in entries} == {f"item {number}" for number in range(8)}
    assert all(entry["project"] == "project" and entry["branch"] == "unknown" for entry in entries)


def test_done_refuses_an_ambiguous_header_without_writing(tmp_path):
    path, cwd = tmp_path / "deferred.md", tmp_path / "project"
    cwd.mkdir()
    deferred.add("same task", by="owner", path=path, cwd=cwd)
    deferred.add("same task", by="owner", path=path, cwd=cwd)
    before = path.read_text()
    with pytest.raises(deferred.DeferredError, match="more than one") as exc:
        deferred.done("same task", path=path, removals=tmp_path / "removed.log")
    assert exc.value.args[0].count("project / unknown: same task") == 2
    assert path.read_text() == before
    assert not list(tmp_path.glob("deferred.md.bak-*"))
    assert not (tmp_path / "removed.log").exists()


def test_answer_marks_a_handoff_then_done_backs_it_up_and_logs_it(tmp_path):
    path, removed, cwd = tmp_path / "deferred.md", tmp_path / "removed.log", tmp_path / "source"
    cwd.mkdir()
    added = deferred.add("check the shared setting", by="owner", for_project="receiver", path=path, cwd=cwd)
    answered = deferred.answer(added["header"], "setting is enabled", path=path)
    assert answered["state"] == "answered" and answered["answer"] == "setting is enabled"
    entry = deferred.read(path)[0]
    assert entry["for"] == "receiver" and entry["state"] == "answered"
    removed_entry = deferred.done(added["header"], path=path, removals=removed)
    assert removed_entry["header"] == added["header"] and deferred.read(path) == []
    backups = list(tmp_path.glob("deferred.md.bak-*"))
    assert len(backups) == 1 and "setting is enabled" in backups[0].read_text()
    assert added["header"] in removed.read_text()


def test_cli_add_list_and_no_match_use_the_configured_file(tmp_path, monkeypatch, capsys):
    path = tmp_path / "backlog.md"
    monkeypatch.setenv("CLAUDE_COMPANION_DEFERRED_FILE", str(path))
    assert deferred.main(["add", "--by", "owner", "write a test", "after review"]) == 0
    assert "write a test" in capsys.readouterr().out
    assert deferred.main(["--list"]) == 0
    assert "Pick up when: after review" in capsys.readouterr().out
    assert deferred.main(["--done", "missing entry"]) == 1
    assert "no deferred entry matches" in capsys.readouterr().err
