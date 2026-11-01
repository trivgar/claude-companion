import json
import os
import subprocess

import pytest

from agent_companion import scoreboard


NOW = 2_000_000_000.0


def _git(repo, *args, env=None):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


def _commit(repo, text, subject, at):
    (repo / "work.txt").write_text(text)
    _git(repo, "add", "work.txt")
    env = {**os.environ, "GIT_AUTHOR_DATE": f"@{int(at)}", "GIT_COMMITTER_DATE": f"@{int(at)}"}
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", subject, env=env)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "project"
    repo.mkdir()
    _git(repo, "init", "-q")
    return repo


def _log(base, repo, sha, at, session_id="session-a"):
    base.mkdir()
    (base / "commits.jsonl").write_text(json.dumps({"at": at, "session_id": session_id,
        "cwd": str(repo), "sha": sha, "subject": "write initial work"}) + "\n")


def _row(result, days="7"):
    return result["windows"][days][0]


def test_later_partial_rewrite_is_charged_to_the_original_commit(tmp_path, repository):
    first_at = NOW - 3 * 86400
    first = _commit(repository, "one\ntwo\nthree\n", "initial work", first_at)
    _commit(repository, "one\nchanged\nthree\n", "rewrite one line", first_at + 3600)
    base = tmp_path / "data"
    _log(base, repository, first, first_at)

    result = scoreboard.summary(now=NOW, base=base, projects=[("demo", str(repository))],
                                session_names={"session-a": "active session"})

    row = _row(result)
    assert row["name"] == "active session"
    assert row["commits"] == 1
    assert row["lines_added"] == 3
    assert row["lines_reworked"] == 1
    assert row["rework_percent"] == pytest.approx(33.3)
    assert row["worst"]["sha"] == first


def test_rewrite_outside_the_window_is_not_counted(tmp_path, repository):
    first_at = NOW - 20 * 86400
    first = _commit(repository, "one\ntwo\n", "initial work", first_at)
    _commit(repository, "one\nchanged\n", "late rewrite", first_at + 8 * 86400)
    base = tmp_path / "data"
    _log(base, repository, first, first_at)

    result = scoreboard.summary(now=NOW, base=base, projects=[("demo", str(repository))], window_days=7)

    row = _row(result, "30")
    assert row["lines_added"] == 2
    assert row["lines_reworked"] == 0


def test_marks_round_trip_and_are_counted_but_not_scored(tmp_path, repository):
    first_at = NOW - 10 * 86400
    first = _commit(repository, "one\n", "initial work", first_at)
    base = tmp_path / "data"
    _log(base, repository, first, first_at)

    assert scoreboard.mark(first, "good", base=base, now=NOW) == {"sha": first, "mark": "good"}
    assert scoreboard.marks(base) == {first: "good"}
    assert scoreboard.mark(first, "bad", base=base, now=NOW) == {"sha": first, "mark": "bad"}
    assert json.loads((base / "scoreboard-marks.json").read_text())["marks"] == {first: "bad"}

    row = _row(scoreboard.summary(now=NOW, base=base, projects=[("demo", str(repository))]), "30")
    assert row["good"] == 0 and row["bad"] == 1
    assert row["lines_reworked"] == 0


def test_closed_commit_cache_hit_does_not_run_git(tmp_path, repository, monkeypatch):
    first_at = NOW - 10 * 86400
    first = _commit(repository, "one\ntwo\n", "initial work", first_at)
    _commit(repository, "one\nchanged\n", "rewrite one line", first_at + 3600)
    base = tmp_path / "data"
    _log(base, repository, first, first_at)
    first_result = scoreboard.summary(now=NOW, base=base, projects=[("demo", str(repository))])

    monkeypatch.setattr(scoreboard, "_git", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("git called")))
    second_result = scoreboard.summary(now=NOW + 1, base=base, projects=[("demo", str(repository))])

    assert _row(first_result, "30")["lines_reworked"] == _row(second_result, "30")["lines_reworked"] == 1


def test_unconfigured_repositories_are_listed_as_skipped(tmp_path, repository):
    first_at = NOW - 2 * 86400
    first = _commit(repository, "one\n", "initial work", first_at)
    base = tmp_path / "data"
    _log(base, repository, first, first_at)

    result = scoreboard.summary(now=NOW, base=base, projects=[])

    assert result["windows"]["7"] == []
    assert result["skipped"] == [{"sha": first, "subject": "write initial work", "reason": "not in a configured project"}]


def test_scoreboard_routes_use_owner_read_and_same_origin_write_guards():
    pytest.importorskip("fastapi")
    from agent_companion.server import auth
    from agent_companion.server import scoreboard as route

    get_route = next(item for item in route.router.routes if item.path == "/api/scoreboard" and "GET" in item.methods)
    mark_route = next(item for item in route.router.routes if item.path == "/api/scoreboard/mark" and "POST" in item.methods)
    assert get_route.dependencies[0].dependency is auth.require_token
    assert mark_route.dependencies[0].dependency is auth.require_write


def test_a_commit_from_a_removed_worktree_is_scored_in_its_project(tmp_path, repository):
    first_at = NOW - 3 * 86400
    _commit(repository, "base\n", "base", first_at - 60)
    tree = tmp_path / "trees" / "feature"
    _git(repository, "worktree", "add", "-q", "-b", "feature", str(tree))
    (tree / "work.txt").write_text("one\ntwo\n")
    _git(tree, "add", "work.txt")
    env = {**os.environ, "GIT_AUTHOR_DATE": f"@{int(first_at)}", "GIT_COMMITTER_DATE": f"@{int(first_at)}"}
    _git(tree, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "feature", env=env)
    sha = _git(tree, "rev-parse", "HEAD")
    _git(repository, "merge", "-q", "--ff-only", "feature")
    _git(repository, "worktree", "remove", str(tree))
    _commit(repository, "one\nchanged\n", "rework", first_at + 3600)
    base = tmp_path / "data"
    _log(base, tree, sha, first_at)

    result = scoreboard.summary(now=NOW, base=base, projects=[("demo", str(repository))])

    assert result["skipped_count"] == 0
    row = _row(result)
    assert row["commits"] == 1 and row["lines_reworked"] == 1
    cached = json.loads((base / "scoreboard-cache.json").read_text())
    assert cached["repos"][sha]["project"] == "demo"
