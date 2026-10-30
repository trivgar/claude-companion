"""What each session's repository is actually doing.

Claude Office has a GIT STATUS panel, which is where this idea came from, but
theirs is a single global panel: one branch, one commit list, for one repo. With
eight projects open at once the question is never "what is the git state", it is
"what is THIS session's repo doing" -- and its panel was visibly mixing repos,
showing `main` with one project's commits beside another project's changed files.

So: per session, attached to the session it describes.

WHY THIS SHELLS OUT, WHEN NOTHING ELSE HERE DOES. Branch alone could be read
from .git/HEAD, and the transcripts even carry gitBranch. But ahead/behind means
walking commits against an upstream ref, and a changed-file count means
comparing the index to the worktree -- reimplementing those against a live .git
is how you end up quietly wrong about whether work is pushed, which is exactly
the distinction a dashboard like this exists to show, so this asks git.

The cost is contained instead of avoided. Two commands per repo, and only when
the repo has actually moved: the cache is keyed on the mtimes of HEAD, the
index and the refs, so an idle repo is a few stat() calls per poll and no
subprocess at all. Failure of any kind returns None -- a card without a git
line, never a failed request.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

TIMEOUT = 3.0
COMMITS = 10
MIN_INTERVAL = 2.0          # never re-run for the same repo faster than this
# git expands %x00 into a real NUL in its OUTPUT. The format string that asks
# for it must travel as the literal four characters, because argv strings are
# NUL-terminated and cannot contain one.
_NUL = "\x00"
_FMT = "%h%x00%s%x00%an%x00%ct"


def _run(root: Path, *args: str, timeout: float | None = None) -> str | None:
    """Read-only, and it has to stay that way.

    `git status` refreshes the index, which means it takes .git/index.lock --
    so this, polling every repo with a live session, was contending with the
    user's own git. It cost a `git add` here: "Another git process seems to be running
    in this repository." A dashboard must never make the thing it is watching
    harder to use. GIT_OPTIONAL_LOCKS=0 tells git to skip exactly the locks it
    only wanted for the cache write, and report anyway.
    """
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    try:
        # A repo's own config can name a program for status to run
        # (core.fsmonitor); a session may sit in a repo nobody vetted.
        p = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(root), *args], env=env,
                           capture_output=True, text=True, timeout=timeout or TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout if p.returncode == 0 else None


def _runs_programs(root: Path) -> bool:
    """The repo's own config names a program that status would run: a filter
    driver or an fsmonitor. Reading config runs nothing; status could. Such a
    repo gets no git line rather than a status poll.

    Defence in depth, not a boundary: git is only run where a Claude Code
    session already works, which Claude Code made the user trust first and
    where it runs git itself at startup."""
    pat = r"^(filter\..*\.(clean|smudge|process)|core\.fsmonitor)$"
    for scope in ("--local", "--worktree"):
        out = _run(root, "config", scope, "--includes", "--name-only", "--get-regexp", pat)
        if out and out.strip():
            return True
    return False


def repo_root(cwd) -> Path | None:
    """The worktree root for this directory, or None if it is not in a repo.

    --show-toplevel, not a walk up looking for `.git`: in a linked worktree
    `.git` is a FILE pointing elsewhere, and worktrees are common.
    """
    try:
        if not Path(cwd).is_dir():
            return None
    except OSError:
        return None
    out = _run(Path(cwd), "rev-parse", "--show-toplevel")
    if not out or not out.strip():
        return None
    return Path(out.strip())


def _stamp(root: Path) -> tuple:
    """Cheap fingerprint of "has this repo moved". Stat calls, no subprocess.

    The git dir is not always root/.git -- a linked worktree points at
    <main>/.git/worktrees/<name> -- so the paths are resolved once and any that
    are missing simply contribute nothing.
    """
    paths = [root / ".git", root / ".git" / "HEAD", root / ".git" / "index",
             root / ".git" / "refs"]
    out = []
    for p in paths:
        try:
            st = p.stat()
            out.append((str(p), st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((str(p), 0, 0))
    return tuple(out)


def _parse_status(text: str) -> dict:
    """`status --porcelain=v1 --branch` in one pass.

    Line one is `## branch...upstream [ahead 2, behind 1]`; the rest are file
    entries whose first two columns are the index and worktree states.
    """
    branch, ahead, behind, staged, changed = "", 0, 0, 0, 0
    for line in text.splitlines():
        if line.startswith("## "):
            head = line[3:]
            # "No commits yet on master" and detached HEAD both land here.
            if head.startswith("HEAD (no branch)"):
                branch = "HEAD"
            else:
                if "[" in head:
                    marks = head[head.index("[") + 1:].rstrip("]")
                    for part in marks.split(","):
                        part = part.strip()
                        if part.startswith("ahead "):
                            ahead = int(part[6:] or 0)
                        elif part.startswith("behind "):
                            behind = int(part[7:] or 0)
                    head = head[:head.index("[")].strip()
                branch = head.split("...", 1)[0].strip()
                if branch.startswith("No commits yet on "):
                    branch = branch[len("No commits yet on "):]
            continue
        if len(line) < 2:
            continue
        index_col = line[0]
        # Anything not committed counts as changed; the subset already added
        # to the index counts as staged too.
        changed += 1
        if index_col not in (" ", "?"):
            staged += 1
    return {"branch": branch, "ahead": ahead, "behind": behind,
            "staged": staged, "changed": changed}


def _parse_log(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        parts = line.split(_NUL)
        if len(parts) != 4:
            continue
        h, subject, author, when = parts
        try:
            at = float(when)
        except ValueError:
            at = 0.0
        out.append({"hash": h, "subject": subject, "author": author, "at": at})
    return out


class GitState:
    """Per-repo git state, cached against the repo's own mtimes."""

    def __init__(self) -> None:
        # root -> {"stamp", "at", "state"}
        self._cache: dict[str, dict] = {}
        # cwd -> root | None. rev-parse is a subprocess too, and running one per
        # session per poll just to learn a path that never changes is the cost
        # this class exists to avoid.
        self._roots: dict[str, Path | None] = {}
        self.runs = 0            # subprocess batches, so tests can prove caching

    def for_repo(self, cwd) -> dict | None:
        ck = str(cwd)
        if ck in self._roots:
            root = self._roots[ck]
        else:
            root = repo_root(cwd)
            self._roots[ck] = root
        if root is None:
            return None
        key = str(root)
        stamp = _stamp(root)
        now = time.time()
        hit = self._cache.get(key)
        if hit and hit["stamp"] == stamp and now - hit["at"] < max(MIN_INTERVAL, 0):
            return hit["state"]
        if hit and hit["stamp"] == stamp:
            # Unchanged on disk: keep the answer, just refresh when we checked.
            hit["at"] = now
            return hit["state"]

        self.runs += 1
        if _runs_programs(root):
            return None
        # Submodules carry configs of their own: status never looks inside.
        status = _run(root, "-c", "submodule.recurse=false", "status", "--porcelain=v1", "--branch",
                      "--ignore-submodules=all")
        if status is None:
            return None
        state = _parse_status(status)
        log = _run(root, "log", "--no-show-signature", f"-n{COMMITS}", f"--format={_FMT}")
        state["commits"] = _parse_log(log) if log else []
        state["root"] = key
        # Stamp AFTER the commands, not before: `git status` refreshes the
        # index's stat cache and so bumps .git/index itself. Stamping first
        # meant every read invalidated its own cache and the next poll shelled
        # out again -- the caching was decorative.
        self._cache[key] = {"stamp": _stamp(root), "at": now, "state": state}
        return state
