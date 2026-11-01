"""Local, git-backed session scoreboard.

The commit hook is the authority for which commits belong to a Claude Code
session. Git supplies line counts and identifies later rewrites. Nothing here
sends data anywhere or assigns a grade.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from . import config
from . import projects as configured_projects

DEFAULT_WINDOW_DAYS = 7
MAX_COMMITS_PER_REFRESH = 40
CACHE_SECONDS = 60
_SHA = re.compile(r"[0-9a-f]{40}\Z", re.I)
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+")
_BLAME = re.compile(r"^\^?([0-9a-f]{40}) (\d+) \d+(?: \d+)?$")


def data_dir() -> Path:
    """The app data directory, with the same default as the commit hook."""
    value = config.get("data_dir") or Path.home() / ".local" / "share" / "claude-companion"
    return Path(str(value)).expanduser()


def paths(base: Path | None = None) -> dict[str, Path]:
    """Scoreboard files. ``base`` makes all inputs private to a test fixture."""
    root = Path(base) if base is not None else data_dir()
    configured_log = config.get("commit_log_file") if base is None else None
    return {
        "base": root,
        "commits": Path(str(configured_log)).expanduser() if configured_log else root / "commits.jsonl",
        "cache": root / "scoreboard-cache.json",
        "marks": root / "scoreboard-marks.json",
    }


def score_window_days() -> int:
    """Configured rework window, kept bounded for predictable git work."""
    try:
        value = int(config.get("score_window_days", DEFAULT_WINDOW_DAYS))
    except (TypeError, ValueError):
        value = DEFAULT_WINDOW_DAYS
    return min(90, max(1, value))


def _read_json(path: Path, default):
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, separators=(",", ":"), sort_keys=True)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _stamp(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _stamp_value(value: tuple[int, int] | None) -> list[int] | None:
    return list(value) if value is not None else None


def _git(repo: Path, *args: str, timeout: float = 30) -> str:
    """Read from git only. A bad or busy checkout contributes no score."""
    try:
        result = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            errors="replace",
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def _is_ancestor(repo: Path, sha: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", str(repo), "merge-base", "--is-ancestor", sha, "HEAD"],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=30,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _configured(projects: list[tuple[str, str]] | None) -> list[tuple[str, Path]]:
    rows = configured_projects.configured() if projects is None else projects
    out = []
    for name, value in rows:
        if not isinstance(name, str) or not isinstance(value, str) or not name.strip() or not value.strip():
            continue
        try:
            root = Path(value).expanduser().resolve()
        except OSError:
            continue
        if root.is_dir():
            out.append((" ".join(name.split()), root))
    return out


def _project_for(cwd: object, projects: list[tuple[str, Path]]) -> tuple[str, Path] | None:
    if not isinstance(cwd, str) or not cwd.strip():
        return None
    try:
        candidate = Path(cwd).expanduser().resolve()
    except OSError:
        return None
    for name, root in projects:
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        return name, root
    return None


REPO_RECHECK_SECONDS = 3600


def _has_commit(repo: Path, sha: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=10,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _project_by_commit(sha: str, projects: list[tuple[str, Path]], known: dict,
                       now: float) -> tuple[str, Path] | None:
    """A commit made in a worktree (often removed since) belongs to the project
    whose repository has it: worktrees share their main checkout's objects.
    Answers are cached in ``known``; a miss is asked again after an hour."""
    roots = dict(projects)
    hit = known.get(sha)
    if isinstance(hit, dict):
        name = hit.get("project")
        if name in roots:
            return name, roots[name]
        if not name and now - float(hit.get("checked_at") or 0) < REPO_RECHECK_SECONDS:
            return None
    for name, root in projects:
        if _has_commit(root, sha):
            known[sha] = {"project": name, "checked_at": now}
            return name, root
    known[sha] = {"project": "", "checked_at": now}
    return None


def _records(path: Path) -> list[dict]:
    """First hook row for each commit wins, so hook retries do not count twice."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    out, seen = [], set()
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        sha = str(row.get("sha") or "").lower()
        if not _SHA.fullmatch(sha) or sha in seen:
            continue
        try:
            at = float(row.get("at"))
        except (TypeError, ValueError):
            continue
        seen.add(sha)
        out.append({"sha": sha, "at": at, "cwd": row.get("cwd"),
                    "session_id": str(row.get("session_id") or ""),
                    "subject": str(row.get("subject") or "")[:300]})
    return out


def _added_lines(repo: Path, sha: str) -> int:
    output = _git(repo, "show", "--format=", "--numstat", "--no-renames", sha)
    total = 0
    for line in output.splitlines():
        fields = line.split("\t", 2)
        if len(fields) == 3 and fields[0].isdigit():
            total += int(fields[0])
    return total


def _commit_time(repo: Path, sha: str) -> float | None:
    try:
        return float(_git(repo, "show", "-s", "--format=%ct", sha).strip())
    except ValueError:
        return None


def _date(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _later_commits(repo: Path, sha: str, started: float, ends: float) -> list[tuple[str, float]]:
    output = _git(repo, "log", "--format=%H%x00%ct", "--reverse", f"--since={_date(started)}",
                  f"--before={_date(ends)}", f"{sha}..HEAD")
    out = []
    for line in output.splitlines():
        commit, separator, at = line.partition("\0")
        if not separator or not _SHA.fullmatch(commit):
            continue
        try:
            when = float(at)
        except ValueError:
            continue
        if started < when <= ends:
            out.append((commit.lower(), when))
    return out


def _deleted_ranges(repo: Path, sha: str) -> dict[str, list[tuple[int, int]]]:
    """Line ranges deleted from a later commit's first parent, by old path."""
    output = _git(repo, "diff", "--no-ext-diff", "--no-color", "--no-renames", "-U0", f"{sha}^", sha)
    path = None
    out: dict[str, list[tuple[int, int]]] = {}
    for line in output.splitlines():
        if line.startswith("--- a/"):
            path = line[6:]
        elif line.startswith("--- "):
            path = None
        elif path and (match := _HUNK.match(line)):
            start = int(match.group(1))
            count = int(match.group(2)) if match.group(2) is not None else 1
            if count:
                out.setdefault(path, []).append((start, start + count - 1))
    return out


def _reworked_lines(repo: Path, target: str, later: list[tuple[str, float]]) -> int:
    """Unique target lines removed by later commits, using only affected ranges."""
    charged: set[tuple[str, int]] = set()
    for sha, _when in later:
        for path, ranges in _deleted_ranges(repo, sha).items():
            args = ["blame", "--line-porcelain"]
            for first, last in ranges:
                args.extend(("-L", f"{first},{last}"))
            args.extend((f"{sha}^", "--", path))
            current: tuple[str, int] | None = None
            for line in _git(repo, *args).splitlines():
                match = _BLAME.match(line)
                if match:
                    current = (match.group(1).lower(), int(match.group(2)))
                elif line.startswith("\t") and current and current[0] == target:
                    charged.add(current)
                    current = None
    return len(charged)


def _head(repo: Path) -> str:
    value = _git(repo, "rev-parse", "HEAD").strip().lower()
    return value if _SHA.fullmatch(value) else ""


def _score(repo: Path, sha: str, window_days: int, now: float, previous: dict | None,
           source_stamp: tuple[int, int] | None) -> dict | None:
    """Return one commit's score, reusing a stable cache entry when possible."""
    window_seconds = window_days * 86400
    if isinstance(previous, dict) and previous.get("window_days") == window_days:
        try:
            end = float(previous["commit_at"]) + window_seconds
            checked = float(previous.get("checked_at") or 0)
        except (KeyError, TypeError, ValueError):
            end, checked = 0, 0
        if now >= end:
            return previous
        if previous.get("source_stamp") == _stamp_value(source_stamp) and now - checked < CACHE_SECONDS:
            return previous
        head = _head(repo)
        if head and head == previous.get("head"):
            refreshed = dict(previous)
            refreshed["checked_at"] = now
            refreshed["source_stamp"] = _stamp_value(source_stamp)
            return refreshed
    else:
        head = _head(repo)
    if not head or not _is_ancestor(repo, sha):
        return None
    commit_at = _commit_time(repo, sha)
    if commit_at is None:
        return None
    added = _added_lines(repo, sha)
    later = _later_commits(repo, sha, commit_at, commit_at + window_seconds)
    return {"added": added, "reworked": min(added, _reworked_lines(repo, sha, later)),
            "commit_at": commit_at, "head": head, "checked_at": now,
            "source_stamp": _stamp_value(source_stamp), "window_days": window_days}


def marks(base: Path | None = None) -> dict[str, str]:
    """Current owner marks by commit hash, accepting only the two public values."""
    data = _read_json(paths(base)["marks"], {})
    raw = data.get("marks") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    return {str(sha).lower(): value for sha, value in raw.items()
            if _SHA.fullmatch(str(sha)) and value in {"good", "bad"}}


def mark(sha: str, value: str, base: Path | None = None, now: float | None = None) -> dict:
    """Set an owner mark. Marks are separate from every rework calculation."""
    sha = str(sha or "").lower()
    value = str(value or "").lower()
    if not _SHA.fullmatch(sha) or value not in {"good", "bad"}:
        raise ValueError("a mark needs a commit and good or bad")
    path = paths(base)["marks"]
    data = _read_json(path, {})
    entries = data.get("marks") if isinstance(data.get("marks"), dict) else {}
    entries = {str(key).lower(): item for key, item in entries.items()
               if _SHA.fullmatch(str(key)) and item in {"good", "bad"}}
    entries[sha] = value
    _write_json(path, {"marks": entries, "updated_at": time.time() if now is None else now})
    return {"sha": sha, "mark": value}


def _group(rows: list[dict], since: float, names: dict[str, str], marked: dict[str, str]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for row in rows:
        if row["at"] < since:
            continue
        session_id = row["session_id"]
        key = session_id or ""
        label = names.get(session_id) or session_id or "Unknown session"
        group = grouped.setdefault(key, {"session_id": session_id, "name": names.get(session_id) or "",
                                        "label": label, "commits": 0, "lines_added": 0,
                                        "lines_reworked": 0, "good": 0, "bad": 0, "rows": []})
        score = row.get("score") or {}
        added = int(score.get("added") or 0)
        reworked = int(score.get("reworked") or 0)
        value = marked.get(row["sha"], "")
        group["commits"] += 1
        group["lines_added"] += added
        group["lines_reworked"] += reworked
        if value in {"good", "bad"}:
            group[value] += 1
        rate = round(reworked * 100 / added, 1) if added else None
        group["rows"].append({"sha": row["sha"], "subject": row["subject"], "at": row["at"],
                              "lines_added": added, "lines_reworked": reworked,
                              "rework_percent": rate, "mark": value})
    for group in grouped.values():
        group["rows"].sort(key=lambda row: row["at"], reverse=True)
        group["rework_percent"] = (round(group["lines_reworked"] * 100 / group["lines_added"], 1)
                                    if group["lines_added"] else None)
        group["worst"] = max(group["rows"], key=lambda row: ((row["rework_percent"] or 0),
                                                                row["lines_reworked"], row["at"]))
    return sorted(grouped.values(), key=lambda group: (-group["commits"], group["label"].casefold()))


def summary(now: float | None = None, base: Path | None = None, projects: list[tuple[str, str]] | None = None,
            session_names: dict[str, str] | None = None, window_days: int | None = None) -> dict:
    """Refresh a bounded number of git scores and return the 7-day and 30-day rows."""
    now = time.time() if now is None else now
    window_days = score_window_days() if window_days is None else max(1, int(window_days))
    locations = paths(base)
    source_stamp = _stamp(locations["commits"])
    cache = _read_json(locations["cache"], {})
    scores = cache.get("scores") if isinstance(cache.get("scores"), dict) else {}
    allowed = _configured(projects)
    repos = cache.get("repos") if isinstance(cache.get("repos"), dict) else {}
    repos_before = json.dumps(repos, sort_keys=True)
    rows, skipped = [], []
    for record in _records(locations["commits"]):
        project = (_project_for(record["cwd"], allowed)
                   or _project_by_commit(record["sha"], allowed, repos, now))
        if project is None:
            skipped.append({"sha": record["sha"], "subject": record["subject"],
                            "reason": "not in a configured project"})
            continue
        record["repo_name"], record["repo"] = project
        rows.append(record)

    stale = []
    for row in rows:
        previous = scores.get(row["sha"])
        if not isinstance(previous, dict) or previous.get("window_days") != window_days:
            stale.append(row)
            continue
        try:
            closed = now >= float(previous["commit_at"]) + window_days * 86400
        except (KeyError, TypeError, ValueError):
            stale.append(row)
            continue
        if not closed and (previous.get("source_stamp") != _stamp_value(source_stamp)
                           or now - float(previous.get("checked_at") or 0) >= CACHE_SECONDS):
            stale.append(row)
        row["score"] = previous
    stale.sort(key=lambda row: row["at"], reverse=True)
    refreshed = 0
    for row in stale[:MAX_COMMITS_PER_REFRESH]:
        previous = scores.get(row["sha"])
        score = _score(row["repo"], row["sha"], window_days, now,
                       previous if isinstance(previous, dict) else None, source_stamp)
        if score is not None:
            scores[row["sha"]] = score
            row["score"] = score
            refreshed += 1
    if refreshed or json.dumps(repos, sort_keys=True) != repos_before:
        _write_json(locations["cache"], {"scores": scores, "repos": repos})

    names = {str(key): str(value) for key, value in (session_names or {}).items() if value}
    marked = marks(base)
    windows = {str(days): _group(rows, now - days * 86400, names, marked) for days in (7, 30)}
    return {"windows": windows, "skipped": skipped, "skipped_count": len(skipped),
            "pending": max(0, len(stale) - refreshed), "window_days": window_days}
