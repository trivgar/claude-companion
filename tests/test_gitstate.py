import subprocess
import time

import pytest

from claude_companion.gitstate import GitState, repo_root


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture(autouse=True)
def _own_identity(monkeypatch):
    # The merge queue sets GIT_AUTHOR_NAME=mergeq; it would beat the repo's user.name.
    for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / "work"
    d.mkdir()
    _git(d, "init", "-q", "-b", "master")
    _git(d, "config", "user.email", "t@example.com")
    _git(d, "config", "user.name", "Tester")
    (d / "a.txt").write_text("one\n")
    _git(d, "add", "a.txt")
    _git(d, "commit", "-qm", "first commit")
    return d


def test_reports_the_branch_and_a_clean_tree(repo):
    g = GitState()
    s = g.for_repo(repo)
    assert s["branch"] == "master"
    assert s["changed"] == 0 and s["staged"] == 0
    assert s["ahead"] == 0 and s["behind"] == 0


def test_counts_changed_and_staged_separately(repo):
    (repo / "a.txt").write_text("two\n")          # modified, unstaged
    (repo / "b.txt").write_text("new\n")          # untracked
    _git(repo, "add", "b.txt")                    # ...now staged
    g = GitState()
    s = g.for_repo(repo)
    # One file waiting to be committed, one edited but not added.
    assert s["staged"] == 1, s
    assert s["changed"] == 2, s                   # both count as "not committed"


def test_recent_commits_come_back_newest_first(repo):
    (repo / "a.txt").write_text("two\n")
    _git(repo, "commit", "-aqm", "second commit")
    g = GitState()
    s = g.for_repo(repo)
    assert [c["subject"] for c in s["commits"]][:2] == ["second commit", "first commit"]
    assert s["commits"][0]["author"] == "Tester"
    assert len(s["commits"][0]["hash"]) >= 7


def test_ahead_of_its_upstream_is_counted(repo, tmp_path):
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "-u", "origin", "master")
    (repo / "a.txt").write_text("three\n")
    _git(repo, "commit", "-aqm", "unpushed")
    s = GitState().for_repo(repo)
    assert s["ahead"] == 1, s
    assert s["behind"] == 0, s


def test_a_directory_that_is_not_a_repo_is_not_an_error(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert GitState().for_repo(plain) is None
    assert repo_root(plain) is None


def test_a_missing_directory_is_not_an_error(tmp_path):
    assert GitState().for_repo(tmp_path / "gone") is None


def test_the_second_call_is_served_from_cache(repo):
    g = GitState()
    first = g.for_repo(repo)
    calls_before = g.runs
    second = g.for_repo(repo)
    assert second == first
    assert g.runs == calls_before, "a cached read must not shell out again"


def test_a_commit_invalidates_the_cache(repo):
    g = GitState()
    g.for_repo(repo)
    before = g.runs
    time.sleep(0.01)
    (repo / "a.txt").write_text("changed\n")
    _git(repo, "commit", "-aqm", "third commit")
    s = g.for_repo(repo)
    assert g.runs > before, "git state changed but the cache was not refreshed"
    assert s["commits"][0]["subject"] == "third commit"


def test_a_worktree_resolves_to_its_own_root(repo, tmp_path):
    """With worktrees, a session's cwd is the worktree,
    not the main checkout, and its branch is the one that matters."""
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "side", str(wt))
    s = GitState().for_repo(wt)
    assert s["branch"] == "side", s
    assert repo_root(wt) == wt


def test_polling_never_takes_the_index_lock(tmp_path, monkeypatch):
    """`git status` refreshes the index and takes .git/index.lock to write it
    back, so polling every repo with a live session fought the user's own git --
    it cost a `git add` with "Another git process seems to be running". A
    dashboard must not make the repo it watches harder to use."""
    import claude_companion.gitstate as gs

    seen = {}

    def fake_run(cmd, env=None, **kw):
        seen["env"] = env or {}
        class P:
            returncode, stdout = 0, ""
        return P()

    monkeypatch.setattr(gs.subprocess, "run", fake_run)
    gs._run(tmp_path, "status", "--porcelain=v1")
    assert seen["env"].get("GIT_OPTIONAL_LOCKS") == "0"


def test_the_real_git_honours_it(tmp_path):
    """Belt and braces: prove the flag reaches git and it still reports, rather
    than trusting that the variable spelled right does the right thing."""
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "f.txt").write_text("hi\n")

    import claude_companion.gitstate as gs
    out = gs._run(tmp_path, "status", "--porcelain=v1")
    assert out is not None and "f.txt" in out
    assert not (tmp_path / ".git" / "index.lock").exists()


def test_a_repos_own_fsmonitor_never_runs(repo, tmp_path):
    ran = tmp_path / "ran"
    hook = tmp_path / "fsmon.sh"
    hook.write_text(f"#!/bin/sh\ntouch {ran}\n")
    hook.chmod(0o755)
    _git(repo, "config", "core.fsmonitor", str(hook))
    (repo / "new.txt").write_text("x")
    assert GitState().for_repo(repo) is None
    assert not ran.exists(), "git status ran the repo's own fsmonitor command"


def test_a_repos_own_filter_never_runs(repo, tmp_path):
    ran = tmp_path / "ran"
    (repo / ".gitattributes").write_text("* filter=x\n")
    (repo / "f").write_text("a\n")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "f")
    _git(repo, "config", "filter.x.clean", f"sh -c 'touch {ran}; cat'")
    (repo / "f").write_text("bb\n")
    assert GitState().for_repo(repo) is None
    assert not ran.exists(), "git status ran the repo's own filter"


def test_a_worktree_config_filter_counts_too(repo, tmp_path):
    _git(repo, "config", "extensions.worktreeConfig", "true")
    _git(repo, "config", "--worktree", "filter.x.clean", "cat")
    assert GitState().for_repo(repo) is None


def test_a_signed_commit_never_runs_the_repos_gpg_program(repo, tmp_path):
    ran = tmp_path / "ran"
    gpg = tmp_path / "gpg.sh"
    gpg.write_text(f"#!/bin/sh\ntouch {ran}\n")
    gpg.chmod(0o755)
    tree = subprocess.run(["git", "-C", str(repo), "write-tree"], capture_output=True, text=True, check=True).stdout.strip()
    body = (f"tree {tree}\nauthor a <a@b> 1 +0000\ncommitter a <a@b> 1 +0000\n"
            "gpgsig -----BEGIN PGP SIGNATURE-----\n \n x\n -----END PGP SIGNATURE-----\n\nsigned\n")
    c = subprocess.run(["git", "-C", str(repo), "hash-object", "-t", "commit", "-w", "--stdin"],
                       input=body, capture_output=True, text=True, check=True).stdout.strip()
    _git(repo, "update-ref", "HEAD", c)
    _git(repo, "config", "log.showSignature", "true")
    _git(repo, "config", "gpg.program", str(gpg))
    assert GitState().for_repo(repo) is not None
    assert not ran.exists(), "git log ran the repo's own gpg.program"
