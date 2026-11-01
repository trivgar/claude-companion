"""The merge queue: how work from two people's Claudes lands on main.

Every job happens in its own worktree on its own branch. A session whose own
targeted tests pass runs `python -m agent_companion.mergeq add` and moves on.
`mergeq run` (by hand, or the systemd timer) is the one merger: it takes the
queued branches or existing PRs in order, each pinned to the sha it was queued
at, merges them in an integration worktree of its own, runs the whole suite,
and for each branch that merged cleanly pushes it, opens a PR to main and
merges that PR, only if the suite was green. Existing PRs are merged directly.
It never resolves a conflict, never edits code, never pushes main (main moves
only through `gh pr merge`) and never forces.

Red: the batch is replayed one branch at a time, each followed by the suite;
the branch that turns it red is dropped and reported, the rest still land.
Stdlib only. State lives in one directory (AGENT_COMPANION_MERGEQ_DIR, default
~/.local/share/agent-companion/mergeq).
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from . import config

SUITE = "python3 -m pytest -q -x"
UNIT = "agent-companion"
HISTORY = 40
QUEUED, MERGED, CONFLICT, FAILED, PUSH_FAILED = "queued", "merged", "conflict", "failed", "push-failed"


def state_dir() -> Path:
    return Path(config.env("MERGEQ_DIR")
                or Path.home() / ".local" / "share" / "agent-companion" / "mergeq")


def _env() -> dict:
    e = dict(os.environ)
    for k, v in (("GIT_AUTHOR_NAME", "mergeq"), ("GIT_COMMITTER_NAME", "mergeq"),
                 ("GIT_AUTHOR_EMAIL", "mergeq@localhost"), ("GIT_COMMITTER_EMAIL", "mergeq@localhost")):
        e.setdefault(k, v)
    e["GIT_TERMINAL_PROMPT"] = "0"
    return e


def _run(args, cwd=None, timeout=None, shell=False):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=_env(),
                          stdin=subprocess.DEVNULL, timeout=timeout, shell=shell)


def git(cwd, *args, check=True) -> str:
    r = _run(["git", "-C", str(cwd), *args])
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip() if r.returncode == 0 else ""


def main_checkout(start: Path | None = None) -> Path:
    """The primary checkout: the first entry of `git worktree list`."""
    start = Path(start or config.env("MERGEQ_REPO") or ".")
    out = git(start, "worktree", "list", "--porcelain")
    for line in out.splitlines():
        if line.startswith("worktree "):
            return Path(line[9:])
    raise RuntimeError("not inside a git repository")


# --- the queue ------------------------------------------------------------------

class Store:
    """queue.json, last.json, history.json and a log, written under a flock."""

    def __init__(self, base: Path | None = None):
        self.dir = Path(base or state_dir())
        self.dir.mkdir(parents=True, exist_ok=True)

    def lock(self, name: str = ".lock", blocking: bool = True):
        f = open(self.dir / name, "a+")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            f.close()
            return None
        return f

    def _read(self, name, default):
        try:
            return json.loads((self.dir / name).read_text())
        except (OSError, ValueError):
            return default

    def _write(self, name, data):
        p = self.dir / name
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        os.replace(tmp, p)

    def queue(self) -> list[dict]:
        return self._read("queue.json", [])

    def add(self, branch: str, sha: str, summary: str = "", pr: int | None = None) -> dict:
        f = self.lock()
        try:
            q = [e for e in self.queue() if e["branch"] != branch]   # the newest sha wins
            e = {"branch": branch, "sha": sha, "summary": summary, "added": time.time()}
            if pr is not None:
                e["pr"] = pr
            q.append(e)
            self._write("queue.json", q)
            return e
        finally:
            f.close()

    def remove(self, entries: list[dict]) -> None:
        """Drop exactly these (branch, sha) pairs; a newer sha queued meanwhile stays."""
        gone = {(e["branch"], e["sha"]) for e in entries}
        f = self.lock()
        try:
            self._write("queue.json", [e for e in self.queue() if (e["branch"], e["sha"]) not in gone])
        finally:
            f.close()

    def last(self):
        return self._read("last.json", None)

    def history(self) -> list[dict]:
        return self._read("history.json", [])

    def record(self, result: dict) -> None:
        self._write("last.json", result)
        self._write("history.json", (self.history() + result.get("outcomes", []))[-HISTORY:])

    def log(self, msg: str) -> None:
        with open(self.dir / "mergeq.log", "a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")


# --- gh ------------------------------------------------------------------------

def gh_ready(gh: str = "gh") -> str:
    """'' when gh is installed and signed in, else the reason."""
    if not shutil.which(gh):
        return "gh is not installed (https://cli.github.com); nothing changed"
    try:
        r = _run([gh, "auth", "status"], timeout=20)
    except (OSError, subprocess.SubprocessError):
        return "gh did not answer; nothing changed"
    return "" if r.returncode == 0 else "gh is not signed in (run `gh auth login`); nothing changed"


def _pr_view(repo, gh: str, number: int, fields: str) -> tuple[dict | None, str]:
    """Return selected PR fields, or a user-facing problem."""
    try:
        r = _run([gh, "pr", "view", str(number), "--json", fields], cwd=repo, timeout=120)
    except (OSError, subprocess.SubprocessError) as ex:
        return None, f"gh pr view: {ex}"
    if r.returncode:
        return None, "gh pr view: " + _last_line(r.stderr or r.stdout)
    try:
        data = json.loads(r.stdout or "{}")
    except ValueError:
        return None, "gh pr view: unreadable answer"
    if not isinstance(data, dict):
        return None, "gh pr view: unreadable answer"
    return data, ""


# --- the suite -------------------------------------------------------------------

def run_suite(tree: Path, suite: str, timeout: float = 3600) -> tuple[bool, int, str]:
    """(green, passed count, output tail). Green means exit 0 and a 'N passed' line."""
    try:
        r = _run(suite, cwd=tree, timeout=timeout, shell=True)
    except subprocess.TimeoutExpired:
        return False, 0, "the suite timed out"
    out = (r.stdout or "") + (r.stderr or "")
    m = None
    for m in re.finditer(r"(\d+) passed", out):
        pass
    n = int(m.group(1)) if m else 0
    return (r.returncode == 0 and n > 0), n, out[-1500:]


# --- run ------------------------------------------------------------------------

def _integration(repo: Path, base: Path) -> Path:
    tree = base / "int"
    if not (tree / ".git").exists():
        git(repo, "worktree", "prune")
        git(repo, "worktree", "add", "--detach", str(tree), "origin/main")
    # The main checkout may sit on a Windows drive with core.fileMode false;
    # this tree is on Linux, where a script's bit is what a fresh clone gets.
    git(repo, "config", "extensions.worktreeConfig", "true")
    git(tree, "config", "--worktree", "core.fileMode", "true")
    return tree


def _reset(tree: Path, ref: str) -> None:
    git(tree, "merge", "--abort", check=False)
    git(tree, "checkout", "-q", "--detach", "-f", ref)
    git(tree, "reset", "-q", "--hard", ref)
    git(tree, "clean", "-fdq")


def _merge(tree: Path, e: dict) -> bool:
    r = _run(["git", "-C", str(tree), "merge", "--no-ff", "--no-edit", "-m",
              f"Merge {e['branch']} (merge queue)", e["sha"]])
    if r.returncode:
        git(tree, "merge", "--abort", check=False)
        return False
    return True


def _note_for_partner(e: dict, notify) -> None:
    m = re.match(r"for-([\w-]+):\s*(.*)", e.get("summary", ""), re.S)
    if not m:
        return
    try:
        notify("for-" + m.group(1), f"Merged: {e['branch']}", m.group(2).strip() or e["branch"])
    except Exception:       # a note is a courtesy; never fail a merge over it
        pass


def _default_notify(label: str, title: str, body: str) -> None:
    from agent_companion import collab
    if collab.label() and collab.repo():
        collab.file(label, title, body, collab.repo())


def _restart() -> str:
    if not shutil.which("systemctl"):
        return ""
    try:
        if _run(["systemctl", "--user", "cat", UNIT], timeout=10).returncode:
            return ""
        r = _run(["systemctl", "--user", "restart", UNIT], timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ""
    return "restarted" if r.returncode == 0 else "restart failed"


def _fast_forward(repo: Path) -> str:
    if git(repo, "rev-parse", "--abbrev-ref", "HEAD", check=False) != "main":
        return "main checkout not on main; left alone"
    if git(repo, "status", "--porcelain", "--untracked-files=no", check=False):
        return "main checkout has changes; left alone"
    r = _run(["git", "-C", str(repo), "merge", "--ff-only", "-q", "origin/main"])
    return "main checkout fast-forwarded" if r.returncode == 0 else "main checkout could not fast-forward"


def run(repo: Path | None = None, base: Path | None = None, suite: str = SUITE, if_queued: bool = False,
        gh: str = "gh", notify=_default_notify, say=print) -> int:
    """Returns 0 for nothing wrong, 1 when a branch was dropped, 2 when it could not run."""
    store = Store(base)
    if if_queued and not store.queue():
        return 0
    why = gh_ready(gh)
    if why:
        say(why)
        return 2
    lock = store.lock("run.lock", blocking=False)
    if lock is None:
        say("another merger is running")
        return 0
    try:
        return _run_locked(store, Path(repo) if repo else main_checkout(), suite, gh, notify, say)
    finally:
        lock.close()


def _run_locked(store, repo, suite, gh, notify, say) -> int:
    batch = store.queue()
    if not batch:
        say("queue is empty")
        return 0
    started = time.time()
    outcomes: list[dict] = []

    def out(e, state, detail=""):
        o = {"branch": e["branch"], "sha": e["sha"][:12], "state": state, "detail": detail,
             "summary": e.get("summary", ""), "at": time.time()}
        outcomes.append(o)
        store.log(f"{e['branch']} {e['sha'][:12]}: {state} {detail}".strip())
        say(f"{e['branch']}: {state}" + (f" ({detail})" if detail else ""))

    def finish(code, **extra):
        store.record({"started": started, "finished": time.time(), "outcomes": outcomes, **extra})
        return code

    try:
        git(repo, "fetch", "-q", "origin")
        tree = _integration(repo, store.dir)
        _reset(tree, "origin/main")
    except RuntimeError as ex:
        say(f"cannot prepare the integration worktree: {ex}")
        store.log(f"prepare failed: {ex}")
        return 2

    # 1. merge everything, drop conflicts, run the suite once.
    merged, dropped = [], []
    for e in batch:
        if _merge(tree, e):
            merged.append(e)
        else:
            dropped.append(e)
            out(e, CONFLICT, "does not merge with main plus the branches queued before it")
    passed = 0
    green = False
    if merged:
        green, passed, tail = run_suite(tree, suite)
        # 2. red: replay one branch at a time; a branch that turns it red is dropped.
        if not green and len(merged) > 1:
            _reset(tree, "origin/main")
            keep = []
            for e in merged:
                before = git(tree, "rev-parse", "HEAD")
                if not _merge(tree, e):
                    _reset(tree, before)
                    out(e, CONFLICT, "does not merge during the replay")
                    continue
                ok, n, tail = run_suite(tree, suite)
                if ok:
                    keep.append(e)
                    passed = n
                else:
                    _reset(tree, before)
                    out(e, FAILED, "the suite went red with this branch: " + _last_line(tail))
            merged, green = keep, bool(keep)
        elif not green:
            out(merged[0], FAILED, "the suite went red with this branch: " + _last_line(tail))
            merged = []
    if not (green and merged):
        store.remove(batch)         # every entry has an outcome above
        say("nothing merged")
        return finish(1 if outcomes else 0, suite_green=False)

    # 3. green: push local branches and create their PRs, or merge queued PRs.
    landed = 0
    waiting: list[dict] = []
    for i, e in enumerate(merged):
        b = e["branch"]
        if e.get("pr") is not None:
            problem = _merge_queued_pr(repo, gh, e)
        else:
            p = _run(["git", "-C", str(repo), "push", "origin", f"{e['sha']}:refs/heads/{b}"])
            if p.returncode:
                problem = _last_line(p.stderr)
            else:
                problem = _open_and_merge(repo, gh, e, passed)
                if not problem:
                    d = _run(["git", "-C", str(repo), "push", "origin", "--delete", b])
                    if d.returncode:
                        store.log(f"{b}: remote branch delete failed: {_last_line(d.stderr)}")
        if problem:
            out(e, PUSH_FAILED, problem)
            waiting = merged[i + 1:]
            if waiting:
                say(f"stopped at {b}: {len(waiting)} branch(es) stay queued for the next run, "
                    "since they were tested together with it")
                store.log("stopped at " + b + "; still queued: " + ", ".join(w["branch"] for w in waiting))
            break
        landed += 1
        out(e, MERGED, f"{passed} passed")
        _note_for_partner(e, notify)
    bad = {o["branch"] for o in outcomes if o["state"] != MERGED}
    store.remove([e for e in batch if e not in waiting])
    after = ""
    if landed:
        git(repo, "fetch", "-q", "origin", check=False)
        after = _fast_forward(repo)
        r = _restart()
        after = (after + "; " + r).strip("; ")
        say(after)
    return finish(1 if bad else 0, suite_green=True, passed=passed, after=after,
                  waiting=[w["branch"] for w in waiting])


CHECKS_WAIT_S = 30 * 60
NO_CHECKS_GRACE_S = 90     # a PR opened a moment ago may not have its checks registered yet


def _await_checks(repo, gh, number) -> str:
    """'' once the PR's required checks pass, or when the branch requires none.
    main may require CI (trivgar/agent-companion does since 2026-11-01), and on a
    PR opened a moment ago it has only just started, so GitHub refuses the merge."""
    give_up = time.time() + NO_CHECKS_GRACE_S
    while True:
        try:
            r = _run([gh, "pr", "checks", str(number), "--required", "--watch", "--interval", "20"],
                     cwd=repo, timeout=CHECKS_WAIT_S)
        except subprocess.TimeoutExpired:
            return f"PR #{number}: required checks still running after {CHECKS_WAIT_S // 60} min; not merged"
        if not r.returncode:
            return ""
        said = (r.stdout or "") + (r.stderr or "")
        if "no required checks" not in said and "no checks reported" not in said:
            return f"PR #{number}: required checks did not pass ({_last_line(said)}); not merged"
        # None yet: either the branch requires none, or GitHub has not started
        # them. Wait out the grace, then let the merge itself say which.
        if time.time() >= give_up:
            return ""
        time.sleep(15)


def _open_and_merge(repo, gh, e, passed) -> str:
    """Open the PR and merge it only at the tested sha. '' on success, else the problem."""
    b, sha = e["branch"], e["sha"]
    body = (e.get("summary") or b) + f"\n\nTests: {passed} passed on the integration run"
    title = " ".join((e.get("summary") or "").split())[:72] or _subject(repo, sha or b) or b
    c = _run([gh, "pr", "create", "--head", b, "--base", "main", "--title", title, "--body", body], cwd=repo, timeout=120)
    if c.returncode and "already exists" not in (c.stderr or ""):
        return "gh pr create: " + _last_line(c.stderr)
    m = re.search(r"/pull/(\d+)", c.stdout or "")
    want = int(m.group(1)) if m else None
    lst = _run([gh, "pr", "list", "--head", b, "--state", "open", "--json",
                "number,headRefOid,headRepositoryOwner,isCrossRepository"], cwd=repo, timeout=120)
    if lst.returncode:
        return "gh pr list: " + _last_line(lst.stderr)
    try:
        prs = [x for x in json.loads(lst.stdout or "[]") if want is None or x.get("number") == want]
    except ValueError:
        return "gh pr list: unreadable answer"
    if len(prs) != 1:
        return f"gh pr list: expected one open PR for {b}, found {len(prs)}"
    pr = prs[0]
    if pr.get("headRefOid") != sha:
        return f"PR #{pr.get('number')} head is {str(pr.get('headRefOid'))[:12]}, not the tested {sha[:12]}; not merged"
    if pr.get("isCrossRepository"):
        return f"PR #{pr.get('number')} comes from another repository; not merged"
    waited = _await_checks(repo, gh, pr["number"])
    if waited:
        return waited
    r = _run([gh, "pr", "merge", str(pr["number"]), "--merge", "--match-head-commit", sha],
             cwd=repo, timeout=120)
    if not r.returncode:
        return ""
    view = _run([gh, "pr", "view", str(pr["number"]), "--json", "state"], cwd=repo, timeout=120)
    try:
        if not view.returncode and json.loads(view.stdout or "{}").get("state") == "MERGED":
            return ""
    except (AttributeError, ValueError):
        pass
    return "gh pr merge: " + _last_line(r.stderr)


def _merge_queued_pr(repo, gh, e) -> str:
    """Merge an already-open same-repository PR at the sha the queue tested."""
    number, sha = e["pr"], e["sha"]
    waited = _await_checks(repo, gh, number)
    if waited:
        return waited
    r = _run([gh, "pr", "merge", str(number), "--merge", "--match-head-commit", sha],
             cwd=repo, timeout=120)
    if not r.returncode:
        return ""
    pr, view_problem = _pr_view(repo, gh, number, "headRefOid,state,isCrossRepository")
    if pr:
        if pr.get("state") != "OPEN":
            return f"PR #{number} is {pr.get('state')}, no longer open; not merged"
        if pr.get("isCrossRepository"):
            return f"PR #{number} comes from another repository; not merged"
        head = pr.get("headRefOid")
        if head != sha:
            return (f"PR #{number} head moved since it was pinned ({str(head)[:12]}, not {sha[:12]}); "
                    "not merged because --match-head-commit protects the tested commit")
    detail = _last_line(r.stderr)
    return "gh pr merge: " + detail + (f" ({view_problem})" if view_problem else "")


def _subject(repo, rev) -> str:
    """The commit's subject line, for a PR title when the queue has no summary."""
    c = _run(["git", "log", "-1", "--format=%s", str(rev)], cwd=repo, timeout=30)
    return (c.stdout or "").strip()[:72] if not c.returncode else ""


def _last_line(text: str) -> str:
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    return lines[-1][:200] if lines else ""


# --- the CLI ----------------------------------------------------------------------

def _queue_pr(store: Store, repo: Path, number: int, summary: str) -> tuple[int, str]:
    if number <= 0:
        return 1, "PR number must be positive"
    pr, problem = _pr_view(repo, "gh", number,
                           "headRefName,headRefOid,isCrossRepository,state,title")
    if not pr:
        return 1, problem
    state = pr.get("state")
    if state != "OPEN":
        return 1, f"refusing PR #{number}: it is {state or 'not open'}, not open"
    if pr.get("isCrossRepository"):
        return 1, f"refusing PR #{number}: it comes from another repository"
    branch, sha = pr.get("headRefName"), pr.get("headRefOid")
    if not isinstance(branch, str) or not branch or not isinstance(sha, str) or not sha:
        return 1, f"refusing PR #{number}: GitHub did not return a head branch and commit"
    try:
        # Same-repository PRs can be fetched by their head branch. This also
        # leaves the pinned commit available after the collaborator moves on.
        git(repo, "fetch", "origin", branch)
        git(repo, "cat-file", "-e", sha + "^{commit}")
    except RuntimeError as ex:
        return 1, f"cannot fetch PR #{number} head: {ex}"
    entry = store.add(branch, sha, summary or str(pr.get("title") or ""), pr=number)
    return 0, f"queued PR #{number} ({branch}) at {entry['sha'][:12]} ({len(store.queue())} in the queue)"


def _age(t: float) -> str:
    s = int(time.time() - t)
    return f"{s // 3600}h" if s >= 3600 else f"{s // 60}m" if s >= 60 else f"{s}s"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agent_companion.mergeq")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="queue a finished branch, pinned to its current sha")
    a.add_argument("branch", nargs="?")
    a.add_argument("--pr", type=int, metavar="NUMBER", help="queue an existing same-repository PR")
    a.add_argument("--summary", default="")
    r = sub.add_parser("run", help="merge the queue (the timer runs this)")
    r.add_argument("--if-queued", action="store_true", help="exit quietly when the queue is empty")
    r.add_argument("--suite", default=SUITE)
    sub.add_parser("list", help="what is queued")
    s = sub.add_parser("status", help="queued and recent outcomes")
    s.add_argument("--json", action="store_true")
    ns = ap.parse_args(argv)
    store = Store()
    if ns.cmd == "add":
        if ns.pr is not None:
            if ns.branch:
                print("use either a branch or --pr, not both", file=sys.stderr)
                return 1
            code, message = _queue_pr(store, Path("."), ns.pr, ns.summary)
            print(message, file=sys.stderr if code else sys.stdout)
            return code
        try:
            branch = ns.branch or git(".", "rev-parse", "--abbrev-ref", "HEAD")
            if branch in ("main", "HEAD"):
                print("refusing to queue main or a detached HEAD", file=sys.stderr)
                return 1
            sha = git(".", "rev-parse", "--verify", branch + "^{commit}")
        except RuntimeError as ex:
            print(ex, file=sys.stderr)
            return 1
        e = store.add(branch, sha, ns.summary)
        print(f"queued {branch} at {sha[:12]} ({len(store.queue())} in the queue)")
        return 0
    if ns.cmd == "run":
        try:
            return run(suite=ns.suite, if_queued=ns.if_queued)
        except RuntimeError as ex:
            print(ex, file=sys.stderr)
            return 2
    q, last, hist = store.queue(), store.last(), store.history()
    if ns.cmd == "status" and ns.json:
        print(json.dumps({"queued": q, "last": last, "recent": hist}, indent=1))
        return 0
    print(f"{len(q)} queued")
    for e in q:
        print(f"  {e['branch']} {e['sha'][:12]} {_age(e['added'])} ago  {e.get('summary', '')}")
    if ns.cmd == "status":
        print("recent")
        for o in hist[-10:]:
            print(f"  {o['branch']} {o['sha']} {o['state']} {_age(o['at'])} ago  {o.get('detail', '')}")
        if not hist:
            print("  none yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
