import os
import subprocess

import pytest

from claude_companion import changes as ch


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """A clone with an upstream, one commit pushed, one not."""
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    d = tmp_path / "work"
    d.mkdir()
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "t@example.com")
    _git(d, "config", "user.name", "Tester")
    (d / "a.txt").write_text("one\n")
    _git(d, "add", "a.txt")
    _git(d, "commit", "-qm", "first")
    _git(d, "remote", "add", "origin", str(bare))
    _git(d, "push", "-q", "-u", "origin", "main")
    (d / "b.txt").write_text("bee\n")
    _git(d, "add", "b.txt")
    _git(d, "commit", "-qm", "second, not pushed")
    return d


def test_lists_uncommitted_and_unpushed(repo):
    (repo / "a.txt").write_text("one\ntwo\n")
    (repo / "new.txt").write_text("fresh\n")
    got = ch.changes(repo)
    files = {f["path"]: f for f in got["uncommitted"]}
    assert files["a.txt"]["status"] == "modified" and files["a.txt"]["added"] == 1
    assert files["new.txt"]["status"] == "untracked"
    assert got["base"] == "origin/main"
    assert [c["subject"] for c in got["unpushed"]] == ["second, not pushed"]
    assert got["unpushed"][0]["files"][0]["path"] == "b.txt"


def test_no_upstream_counts_from_origin_main(repo):
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "c.txt").write_text("see\n")
    _git(repo, "add", "c.txt")
    _git(repo, "commit", "-qm", "on the branch")
    got = ch.changes(repo)
    assert got["base"] == "origin/main (no upstream)"
    assert [c["subject"] for c in got["unpushed"]] == ["on the branch", "second, not pushed"]


def test_diff_of_a_listed_file_and_never_another(repo, tmp_path):
    (repo / "a.txt").write_text("one\ntwo\n")
    d = ch.diff(repo, "a.txt")
    assert "+two" in d["diff"] and not d["cut"]
    assert "+bee" in ch.diff(repo, "b.txt")["diff"], "a file only in an unpushed commit"
    secret = tmp_path / "secret.txt"
    secret.write_text("private\n")
    for bad in (str(secret), "../secret.txt", "nope.txt", ""):
        assert "error" in ch.diff(repo, bad), bad


def test_an_untracked_link_is_not_followed(repo, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("private\n")
    os.symlink(secret, repo / "link.txt")
    assert "error" in ch.diff(repo, "link.txt")


def test_a_repo_whose_config_runs_a_program_is_not_read(repo):
    _git(repo, "config", "filter.x.clean", "touch /tmp/should-not-run")
    got = ch.changes(repo)
    assert "skipped" in got and "uncommitted" not in got
    assert "error" in ch.diff(repo, "a.txt")


def test_not_a_repo(tmp_path):
    assert ch.changes(tmp_path) is None
