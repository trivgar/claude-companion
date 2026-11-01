"""What an agent's repository has that its remote does not: the files it has
not committed, and the commits it has not pushed, each file's diff on request.

Read-only, through gitstate's runner (no optional locks, no fsmonitor), and
never in a repo whose config names a program for git to run. A diff is only of
a path this module listed: nothing a caller passes reaches git as a path.
"""
from __future__ import annotations

from pathlib import Path

from agent_companion import gitstate
from agent_companion.gitstate import _runs_programs, repo_root

MAX_COMMITS = 30
DIFF_CAP = 200_000
# Longer than the poll's 3 s: asked for once, on a tap, and git on a Windows
# drive under WSL took 3.0 s for one status (2026-10-05), which the poll's
# limit turned into an empty listing.
TIMEOUT = 15.0


def _run(root: Path, *args: str) -> str | None:
    return gitstate._run(root, *args, timeout=TIMEOUT)
NO_PROGRAMS = ("--no-ext-diff", "--no-textconv")
_STATUS = {"M": "modified", "A": "new", "D": "deleted", "R": "renamed", "C": "copied",
           "U": "conflict", "T": "type changed", "?": "untracked"}


def _numstat(text: str | None) -> dict[str, tuple[int | None, int | None]]:
    out = {}
    for line in (text or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        add, rem, path = parts[0], parts[1], parts[-1]
        out[path] = (int(add) if add.isdigit() else None, int(rem) if rem.isdigit() else None)
    return out


def _uncommitted(root: Path) -> list[dict]:
    status = _run(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if status is None:
        return []
    counts = _numstat(_run(root, "diff", *NO_PROGRAMS, "--numstat", "HEAD"))
    files, items = [], status.split("\0")
    i = 0
    while i < len(items):
        item = items[i]
        i += 1
        if len(item) < 4:
            continue
        code, path = item[:2], item[3:]
        if code[0] in "RC":
            i += 1                                    # -z puts the old name next
        letter = "?" if code == "??" else (code[0] if code[0] != " " else code[1])
        add, rem = counts.get(path, (None, None))
        files.append({"path": path, "status": _STATUS.get(letter, "changed"), "added": add, "removed": rem})
    return files


def _base(root: Path) -> tuple[str, str] | None:
    """(ref the unpushed commits are counted from, what to call it)."""
    up = _run(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if up and up.strip():
        return "@{u}", up.strip()
    for main in ("origin/main", "origin/master"):
        mb = _run(root, "merge-base", "HEAD", main)
        if mb and mb.strip():
            return mb.strip(), f"{main} (no upstream)"
    return None


def _unpushed(root: Path, base: str) -> list[dict]:
    log = _run(root, "log", "--no-show-signature", f"--max-count={MAX_COMMITS}",
               "--format=%x01%h%x00%s%x00%ct", "--numstat", *NO_PROGRAMS, f"{base}..HEAD")
    commits = []
    for chunk in (log or "").split("\x01")[1:]:
        head, _, rest = chunk.partition("\n")
        parts = head.split("\0")
        if len(parts) < 3:
            continue
        files = [{"path": p, "added": a, "removed": r} for p, (a, r) in _numstat(rest).items()]
        commits.append({"sha": parts[0], "subject": parts[1],
                        "at": int(parts[2]) if parts[2].isdigit() else None, "files": files})
    return commits


def changes(cwd) -> dict | None:
    """{repo, branch, uncommitted, base, unpushed} for the repo at cwd; None
    when it is not a repo; {repo, skipped} when its config would run a
    program."""
    root = repo_root(cwd)
    if root is None:
        return None
    if _runs_programs(root):
        return {"repo": root.name, "skipped": "this repo's git config runs programs, so it is not read"}
    branch = (_run(root, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    base = _base(root)
    return {"repo": root.name, "branch": branch, "uncommitted": _uncommitted(root),
            "base": base[1] if base else None, "unpushed": _unpushed(root, base[0]) if base else []}


def diff(cwd, path: str) -> dict:
    """One listed file's diff: uncommitted against HEAD, else its unpushed
    commits against the base. {path, diff, cut} or {error}."""
    root = repo_root(cwd)
    if root is None:
        return {"error": "not a repository"}
    if _runs_programs(root):
        return {"error": "this repo's git config runs programs, so it is not read"}
    path = str(path or "")
    open_files = {f["path"]: f for f in _uncommitted(root)}
    if path in open_files:
        if open_files[path]["status"] == "untracked":
            f = root / path
            # An untracked symlink would read whatever it points at.
            try:
                if f.is_symlink() or not f.resolve().is_relative_to(root.resolve()):
                    return {"error": "a link, not shown"}
                text = f.read_bytes()[:DIFF_CAP + 1].decode("utf-8", "replace")
            except OSError:
                return {"error": "could not read it"}
            body = "".join(f"+{line}\n" for line in text[:DIFF_CAP].splitlines())
            return {"path": path, "diff": body, "cut": len(text) > DIFF_CAP}
        out = _run(root, "diff", *NO_PROGRAMS, "HEAD", "--", path)
    else:
        base = _base(root)
        listed = base and any(f["path"] == path for c in _unpushed(root, base[0]) for f in c["files"])
        if not base or not listed:
            return {"error": "not one of this agent's changed files"}
        out = _run(root, "diff", *NO_PROGRAMS, f"{base[0]}..HEAD", "--", path)
    if out is None:
        return {"error": "git did not answer"}
    return {"path": path, "diff": out[:DIFF_CAP], "cut": len(out) > DIFF_CAP}
