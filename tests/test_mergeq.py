import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from agent_companion import mergeq

SUITE = "if [ -e BAD ]; then echo '1 failed'; exit 1; fi; echo '5 passed in 0.01s'"
ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}


def sh(cwd, *args):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, env={**os.environ, **ENV})
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


FAKE_GH = """#!PYEXE
import json, os, subprocess, sys, tempfile
a = sys.argv[1:]
open(os.environ["FAKE_LOG"], "a").write(json.dumps(a) + "\\n")
if a[:2] == ["auth", "status"]:
    sys.exit(1 if os.environ.get("FAKE_GH_UNAUTHED") else 0)
st = os.environ["FAKE_LOG"] + ".prs"
prs = json.load(open(st)) if os.path.exists(st) else {}
prs.update(json.loads(os.environ.get("FAKE_EXISTING_PRS", "{}")))
origin = os.environ["FAKE_ORIGIN"]
def oid(br):
    r = subprocess.run(["git", "-C", origin, "rev-parse", "refs/heads/" + br], capture_output=True, text=True)
    return r.stdout.strip()
def info(n):
    v = prs[n]
    return v if isinstance(v, dict) else {"branch": v}
def head_oid(br):
    o = os.environ.get("FAKE_HEAD_OVERRIDE", "")
    brx, _, val = o.partition("=")
    return val if brx == br else oid(br)
def opt(name):
    return a[a.index(name) + 1]
if a[:2] == ["pr", "create"]:
    br = opt("--head")
    if os.environ.get("FAKE_FAIL_CREATE") == br:
        sys.stderr.write("create exploded\\n")
        sys.exit(1)
    n = max([int(x) for x in prs] + [0]) + 1
    prs[str(n)] = br
    json.dump(prs, open(st, "w"))
    print("https://github.com/o/r/pull/%d" % n)
if a[:2] == ["pr", "list"]:
    br = opt("--head")
    print(json.dumps([{"number": int(n), "headRefOid": head_oid(br),
                       "headRepositoryOwner": {"login": "o"}, "isCrossRepository": False}
                      for n in prs if info(n)["branch"] == br]))
if a[:2] == ["pr", "view"]:
    p = info(a[2])
    state = "MERGED" if os.environ.get("FAKE_MERGED_VIEW") == a[2] else p.get("state", "OPEN")
    print(json.dumps({"headRefName": p["branch"], "headRefOid": head_oid(p["branch"]),
                      "isCrossRepository": p.get("isCrossRepository", False), "state": state,
                      "title": p.get("title", "queued change")}))
if a[:2] == ["pr", "merge"]:
    br = info(a[2])["branch"]
    if "--match-head-commit" in a and opt("--match-head-commit") != head_oid(br):
        sys.stderr.write("head moved\\n")
        sys.exit(1)
    d = tempfile.mkdtemp()
    env = dict(os.environ, GIT_AUTHOR_NAME="gh", GIT_AUTHOR_EMAIL="gh@example.com",
               GIT_COMMITTER_NAME="gh", GIT_COMMITTER_EMAIL="gh@example.com")
    for c in (["clone", "-q", origin, d], ["-C", d, "merge", "-q", "--no-ff", "-m", "pr", "origin/" + br],
              ["-C", d, "push", "-q", "origin", "HEAD:main"]):
        if subprocess.run(["git"] + c, env=env, capture_output=True).returncode:
            sys.exit(1)
    if os.environ.get("FAKE_MERGE_EXIT_1") == br:
        sys.stderr.write("merged, but cleanup failed\\n")
        sys.exit(1)
"""


@pytest.fixture
def world(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    sh(tmp_path, "git", "init", "-q", "--bare", "-b", "main", str(origin))
    main = tmp_path / "main"
    sh(tmp_path, "git", "clone", "-q", str(origin), str(main))
    sh(main, "git", "checkout", "-q", "-B", "main")
    (main / "base.txt").write_text("base\n")
    sh(main, "git", "add", "base.txt")
    sh(main, "git", "commit", "-q", "-m", "base")
    sh(main, "git", "push", "-q", "origin", "main")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "gh.jsonl"
    gh = bindir / "gh"
    gh.write_text(FAKE_GH.replace("PYEXE", sys.executable))
    sc = bindir / "systemctl"
    sc.write_text(f"#!/bin/sh\necho \"$@\" >> {tmp_path}/systemctl.log\n")
    for f in (gh, sc):
        f.chmod(f.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("FAKE_ORIGIN", str(origin))
    monkeypatch.setenv("AGENT_COMPANION_MERGEQ_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("FAKE_GH_UNAUTHED", raising=False)
    monkeypatch.delenv("FAKE_EXISTING_PRS", raising=False)

    class W:
        pass
    w = W()
    w.tmp, w.origin, w.main, w.log = tmp_path, origin, main, log
    w.store = mergeq.Store()
    w.said = []
    w.notes = []

    def branch(name, files):
        wt = tmp_path / ("wt-" + name.replace("/", "-"))
        sh(main, "git", "worktree", "add", "-q", "-b", name, str(wt), "main")
        for f, text in files.items():
            (wt / f).write_text(text)
            sh(wt, "git", "add", f)
        sh(wt, "git", "commit", "-q", "-m", "work on " + name)
        return wt, sh(wt, "git", "rev-parse", "HEAD")

    def queue(name, files, summary=""):
        wt, sha = branch(name, files)
        w.store.add(name, sha, summary)
        return wt, sha

    def existing_pr(number, name, files, state="OPEN", is_cross_repository=False, title="queued change"):
        wt, sha = branch(name, files)
        sh(wt, "git", "push", "-q", "origin", f"HEAD:refs/heads/{name}")
        prs = json.loads(os.environ.get("FAKE_EXISTING_PRS", "{}"))
        prs[str(number)] = {"branch": name, "state": state,
                            "isCrossRepository": is_cross_repository, "title": title}
        monkeypatch.setenv("FAKE_EXISTING_PRS", json.dumps(prs))
        return wt, sha

    def go(**kw):
        return mergeq.run(repo=main, base=tmp_path / "state", suite=SUITE, say=w.said.append,
                          notify=lambda *a: w.notes.append(a), **kw)

    def calls(*prefix):
        if not log.exists():
            return []
        return [c for c in map(json.loads, log.read_text().splitlines()) if c[:len(prefix)] == list(prefix)]

    def origin_has(path, ref="main"):
        r = subprocess.run(["git", "-C", str(origin), "cat-file", "-e", f"{ref}:{path}"], capture_output=True)
        return r.returncode == 0

    w.branch, w.queue, w.existing_pr, w.go, w.calls, w.origin_has = branch, queue, existing_pr, go, calls, origin_has
    return w


def test_green_batch_opens_and_merges_prs(world):
    world.queue("feat/a", {"a.txt": "a\n"}, "adds a")
    world.queue("feat/b", {"b.txt": "b\n"}, "adds b")
    assert world.go() == 0
    assert world.origin_has("a.txt") and world.origin_has("b.txt")
    creates = world.calls("pr", "create")
    assert len(creates) == 2
    body = creates[0][creates[0].index("--body") + 1]
    assert "adds a" in body and "Tests: 5 passed on the integration run" in body
    assert "--fill" not in creates[0] and creates[0][creates[0].index("--title") + 1] == "adds a"
    merge = world.calls("pr", "merge")[0]
    assert merge[:2] == ["pr", "merge"] and "--merge" in merge and "--delete-branch" not in merge
    assert "--match-head-commit" in merge
    assert world.store.queue() == []
    assert [o["state"] for o in world.store.last()["outcomes"]] == ["merged", "merged"]
    # the clean main checkout followed, and the server was restarted
    assert (world.main / "a.txt").exists()
    assert "restart agent-companion" in (world.tmp / "systemctl.log").read_text()
    assert [o["branch"] for o in world.store.history()] == ["feat/a", "feat/b"]


def test_red_batch_bisects_and_lands_the_rest(world):
    world.queue("feat/a", {"a.txt": "a\n"})
    world.queue("feat/bad", {"BAD": "x\n"})
    world.queue("feat/c", {"c.txt": "c\n"})
    assert world.go() == 1
    assert world.origin_has("a.txt") and world.origin_has("c.txt")
    assert not world.origin_has("BAD")
    states = {o["branch"]: o["state"] for o in world.store.last()["outcomes"]}
    assert states == {"feat/bad": "failed", "feat/a": "merged", "feat/c": "merged"}
    assert world.store.queue() == []


def test_single_red_branch_merges_nothing(world):
    world.queue("feat/bad", {"BAD": "x\n"})
    assert world.go() == 1
    assert not world.origin_has("BAD")
    assert world.calls("pr", "create") == []
    assert world.store.last()["outcomes"][0]["state"] == "failed"


def test_conflict_is_dropped_and_reported(world):
    world.queue("feat/a", {"base.txt": "from a\n"})
    world.queue("feat/b", {"base.txt": "from b\n"})
    world.queue("feat/c", {"c.txt": "c\n"})
    assert world.go() == 1
    states = {o["branch"]: o["state"] for o in world.store.last()["outcomes"]}
    assert states == {"feat/b": "conflict", "feat/a": "merged", "feat/c": "merged"}
    assert world.origin_has("c.txt")
    assert world.notes == []          # our own problem files nothing to the other side


def test_pinned_sha_ignores_later_commits(world):
    wt, sha = world.queue("feat/a", {"a.txt": "a\n"})
    (wt / "late.txt").write_text("late\n")
    sh(wt, "git", "add", "late.txt")
    sh(wt, "git", "commit", "-q", "-m", "after queueing")
    assert world.go() == 0
    assert world.origin_has("a.txt")
    assert not world.origin_has("late.txt")


def test_requeue_replaces_the_pinned_sha(world):
    wt, sha = world.queue("feat/a", {"a.txt": "a\n"})
    (wt / "late.txt").write_text("late\n")
    sh(wt, "git", "add", "late.txt")
    sh(wt, "git", "commit", "-q", "-m", "more")
    world.store.add("feat/a", sh(wt, "git", "rev-parse", "HEAD"), "again")
    assert len(world.store.queue()) == 1
    world.go()
    assert world.origin_has("late.txt")


def test_gh_missing_stops_and_changes_nothing(world, monkeypatch):
    world.queue("feat/a", {"a.txt": "a\n"})
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    assert world.go(gh="no-such-gh") == 2
    assert "gh is not installed" in world.said[0]
    assert len(world.store.queue()) == 1
    assert not world.origin_has("a.txt")


def test_gh_not_signed_in_stops(world, monkeypatch):
    world.queue("feat/a", {"a.txt": "a\n"})
    monkeypatch.setenv("FAKE_GH_UNAUTHED", "1")
    assert world.go() == 2
    assert "not signed in" in world.said[0]
    assert len(world.store.queue()) == 1
    assert not (world.tmp / "state" / "int").exists()


def test_if_queued_with_empty_queue_is_silent(world):
    assert world.go(if_queued=True) == 0
    assert world.said == [] and world.calls() == []


def test_dirty_main_checkout_is_left_alone(world):
    world.queue("feat/a", {"a.txt": "a\n"})
    (world.main / "base.txt").write_text("edited\n")
    assert world.go() == 0
    assert not (world.main / "a.txt").exists()
    assert "left alone" in world.store.last()["after"]


def test_green_merge_for_partner_files_a_note(world):
    world.queue("feat/a", {"a.txt": "a\n"}, "for-sam: please look at the retry")
    world.go()
    assert world.notes and world.notes[0][0] == "for-sam"


def test_add_cli_pins_branch_and_refuses_main(world, capsys):
    wt, sha = world.branch("feat/z", {"z.txt": "z\n"})
    cwd = os.getcwd()
    os.chdir(wt)
    try:
        assert mergeq.main(["add", "--summary", "zed"]) == 0
        capsys.readouterr()
        assert mergeq.main(["status", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["queued"][0]["branch"] == "feat/z"
    finally:
        os.chdir(cwd)
    q = world.store.queue()
    assert q[0]["branch"] == "feat/z" and q[0]["sha"] == sha and q[0]["summary"] == "zed"
    assert "pr" not in q[0]
    os.chdir(world.main)
    try:
        assert mergeq.main(["add"]) == 1
    finally:
        os.chdir(cwd)


def test_add_pr_pins_the_head_oid(world, capsys):
    _, sha = world.existing_pr(42, "feat/queued-pr", {"queued.txt": "queued\n"},
                               title="queue an existing change")
    cwd = os.getcwd()
    os.chdir(world.main)
    try:
        assert mergeq.main(["add", "--pr", "42"]) == 0
    finally:
        os.chdir(cwd)
    assert "queued PR #42" in capsys.readouterr().out
    entry = world.store.queue()[0]
    assert entry["branch"] == "feat/queued-pr" and entry["sha"] == sha and entry["pr"] == 42
    assert entry["summary"] == "queue an existing change"
    assert world.calls("pr", "view")[0] == [
        "pr", "view", "42", "--json", "headRefName,headRefOid,isCrossRepository,state,title"
    ]


def test_green_queued_pr_merges_that_pr_without_creating_one(world, monkeypatch):
    _, sha = world.existing_pr(42, "feat/queued-pr", {"queued.txt": "queued\n"})
    cwd = os.getcwd()
    os.chdir(world.main)
    try:
        assert mergeq.main(["add", "--pr", "42"]) == 0
    finally:
        os.chdir(cwd)
    commands = []
    real_run = mergeq._run

    def record_run(args, *args_, **kwargs):
        commands.append(args)
        return real_run(args, *args_, **kwargs)

    monkeypatch.setattr(mergeq, "_run", record_run)
    assert world.go() == 0
    assert world.origin_has("queued.txt")
    assert world.calls("pr", "create") == []
    assert world.calls("pr", "merge") == [
        ["pr", "merge", "42", "--merge", "--match-head-commit", sha]
    ]
    assert not any(command[:1] == ["git"] and "push" in command for command in commands)
    assert world.store.last()["outcomes"][0]["state"] == "merged"


@pytest.mark.parametrize(
    ("state", "is_cross_repository", "expected"),
    [("CLOSED", False, "CLOSED"), ("MERGED", False, "MERGED"), ("OPEN", True, "another repository")],
)
def test_add_pr_refuses_closed_merged_or_forked_pr(world, capsys, state, is_cross_repository, expected):
    world.existing_pr(42, "feat/queued-pr", {"queued.txt": "queued\n"}, state=state,
                      is_cross_repository=is_cross_repository)
    cwd = os.getcwd()
    os.chdir(world.main)
    try:
        assert mergeq.main(["add", "--pr", "42"]) == 1
    finally:
        os.chdir(cwd)
    assert expected in capsys.readouterr().err
    assert world.store.queue() == []


def test_moved_queued_pr_head_is_reported_and_not_merged(world):
    wt, sha = world.existing_pr(42, "feat/queued-pr", {"queued.txt": "queued\n"})
    cwd = os.getcwd()
    os.chdir(world.main)
    try:
        assert mergeq.main(["add", "--pr", "42"]) == 0
    finally:
        os.chdir(cwd)
    (wt / "after-queue.txt").write_text("later\n")
    sh(wt, "git", "add", "after-queue.txt")
    sh(wt, "git", "commit", "-q", "-m", "after queueing")
    sh(wt, "git", "push", "-q", "origin", "HEAD:refs/heads/feat/queued-pr")
    assert world.go() == 1
    assert world.calls("pr", "create") == []
    assert world.calls("pr", "merge") == [
        ["pr", "merge", "42", "--merge", "--match-head-commit", sha]
    ]
    outcome = world.store.last()["outcomes"][0]
    assert outcome["state"] == "push-failed" and "head moved since it was pinned" in outcome["detail"]


def test_moved_pr_head_is_refused_and_not_merged(world, monkeypatch):
    _, sha = world.queue("feat/a", {"a.txt": "a\n"})
    monkeypatch.setenv("FAKE_HEAD_OVERRIDE", "feat/a=" + "0" * 40)
    assert world.go() == 1
    assert world.calls("pr", "merge") == []
    assert not world.origin_has("a.txt")
    o = world.store.last()["outcomes"][0]
    assert o["state"] == "push-failed" and "not the tested" in o["detail"]


def test_merge_passes_the_tested_sha(world):
    _, sha = world.queue("feat/a", {"a.txt": "a\n"})
    assert world.go() == 0
    merge = world.calls("pr", "merge")[0]
    assert merge[merge.index("--match-head-commit") + 1] == sha


def test_merged_pr_with_nonzero_gh_exit_carries_on(world, monkeypatch):
    world.queue("feat/a", {"a.txt": "a\\n"})
    world.queue("feat/b", {"b.txt": "b\\n"})
    monkeypatch.setenv("FAKE_MERGE_EXIT_1", "feat/a")
    monkeypatch.setenv("FAKE_MERGED_VIEW", "1")
    assert world.go() == 0
    assert world.origin_has("a.txt") and world.origin_has("b.txt")
    assert world.calls("pr", "view") == [["pr", "view", "1", "--json", "state"]]
    assert [o["state"] for o in world.store.last()["outcomes"]] == ["merged", "merged"]


def test_successful_merge_deletes_only_the_remote_branch(world):
    world.queue("feat/a", {"a.txt": "a\\n"})
    assert world.go() == 0
    deleted = subprocess.run(
        ["git", "-C", str(world.origin), "show-ref", "--verify", "--quiet", "refs/heads/feat/a"]
    )
    assert deleted.returncode == 1


def test_replay_conflict_is_reported_and_not_kept(world, monkeypatch):
    world.queue("feat/a", {"a.txt": "a\n"})
    world.queue("feat/bad", {"BAD": "x\n"})
    real = mergeq._merge
    calls = {"n": 0}

    def flaky(tree, e):
        calls["n"] += 1
        if calls["n"] > 2 and e["branch"] == "feat/a":      # the replay pass only
            return False
        return real(tree, e)
    monkeypatch.setattr(mergeq, "_merge", flaky)
    world.go()
    states = {o["branch"]: o["state"] for o in world.store.last()["outcomes"]}
    assert states["feat/a"] == "conflict"
    assert not world.origin_has("a.txt")


def test_failure_stops_the_run_and_later_branches_stay_queued(world, monkeypatch):
    world.queue("feat/a", {"a.txt": "a\n"})
    world.queue("feat/b", {"b.txt": "b\n"})
    world.queue("feat/c", {"c.txt": "c\n"})
    monkeypatch.setenv("FAKE_FAIL_CREATE", "feat/b")
    assert world.go() == 1
    assert world.origin_has("a.txt") and not world.origin_has("c.txt")
    assert [e["branch"] for e in world.store.queue()] == ["feat/c"]
    states = {o["branch"]: o["state"] for o in world.store.last()["outcomes"]}
    assert states == {"feat/a": "merged", "feat/b": "push-failed"}
    assert world.store.last()["waiting"] == ["feat/c"]
    assert any("feat/b" in m and "stay queued" in m for m in world.said)
