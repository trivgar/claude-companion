"""A small, shared Markdown backlog for work that is parked for later.

The file is deliberately ordinary Markdown.  People can read it without this
module, while this module makes the changes that need a lock (adding, answering
and removing) so two sessions do not lose each other's entries.

Run ``python3 -m claude_companion.deferred --help`` for the command-line
interface.  The dashboard and the Claude Code hook use the same functions.
"""
from __future__ import annotations

import argparse
import fcntl
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import config


MAX_TEXT = 1500
MAX_SHORT_TEXT = 200
_HEAD = re.compile(r"^- \[([^\]]*)\]\s+(.+?)\s*$")
_FIELD = re.compile(r"^\s*([A-Za-z ]+):\s*(.*)$")
_FIELDS = {
    "Project": "project",
    "Branch": "branch",
    "Deferred by": "by",
    "Pick up when": "when",
    "For": "for",
    "State": "state",
    "Ask": "ask",
    "Answer": "answer",
}
_TITLE = "# Deferred work\n\n"


class DeferredError(ValueError):
    """A requested backlog change cannot be made."""


def data_dir() -> Path:
    """The app data directory, without importing the dashboard server."""
    raw = config.get("data_dir") or Path.home() / ".local" / "share" / "claude-companion"
    return Path(str(raw)).expanduser()


def file_path(path: Path | None = None) -> Path:
    """The configured backlog path, or a safe app-data default."""
    if path is not None:
        return Path(path)
    raw = config.get("deferred_file") or data_dir() / "deferred.md"
    return Path(str(raw)).expanduser()


def removals_path(path: Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    raw = config.get("deferred_removals_file") or data_dir() / "deferred-removals.log"
    return Path(str(raw)).expanduser()


def hook_state_dir(path: Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    raw = config.get("deferred_hook_state_dir") or data_dir() / "deferred-hook-seen"
    return Path(str(raw)).expanduser()


def _one_line(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def _timestamp(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def project_of(cwd: str | os.PathLike[str] | None = None) -> str:
    """Git's top-level directory name, falling back to the working directory."""
    where = Path(cwd or os.getcwd())
    try:
        result = subprocess.run(["git", "-C", str(where), "rev-parse", "--show-toplevel"],
                                capture_output=True, text=True, timeout=3, check=False)
        if result.returncode == 0 and result.stdout.strip():
            return _one_line(Path(result.stdout.strip()).name, 120) or "project"
    except (OSError, subprocess.SubprocessError):
        pass
    return _one_line(where.name, 120) or "project"


def branch_of(cwd: str | os.PathLike[str] | None = None) -> str:
    """The current branch, or ``unknown`` outside a Git checkout."""
    where = Path(cwd or os.getcwd())
    try:
        result = subprocess.run(["git", "-C", str(where), "branch", "--show-current"],
                                capture_output=True, text=True, timeout=3, check=False)
        if result.returncode == 0 and result.stdout.strip():
            return _one_line(result.stdout, 200) or "unknown"
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def _entry_title(project: str, branch: str, text: str) -> str:
    return f"{project} / {branch}: {text}"


def _field_name(line: str) -> str | None:
    match = _FIELD.match(line)
    return _FIELDS.get(match.group(1)) if match else None


def parse(text: str) -> list[dict]:
    """Parse entries from our Markdown format, in file order.

    Unknown Markdown remains untouched by edits.  Known fields accept wrapped
    lines so a carefully hand-written entry still appears in the dashboard.
    """
    entries: list[dict] = []
    lines = (text or "").splitlines(keepends=True)
    current: dict | None = None
    last: str | None = None
    fenced = False
    for index, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped.startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = _HEAD.match(raw.rstrip("\r\n"))
        if match:
            if current is not None:
                current["end"] = index
            current = {"timestamp": match.group(1).strip(), "header": match.group(2).strip(),
                       "project": "", "branch": "", "by": "", "when": "", "for": "",
                       "state": "open", "ask": "", "answer": "", "start": index, "end": len(lines)}
            entries.append(current)
            last = None
            continue
        if current is None:
            continue
        field = _field_name(stripped)
        if field:
            value = _FIELD.match(raw).group(2).strip()  # _field_name already checked the match
            current[field] = value
            last = field
        elif stripped and last and raw[:1].isspace():
            current[last] = (str(current.get(last) or "") + " " + stripped).strip()
    if current is not None:
        current["end"] = len(lines)
    for entry in entries:
        if not entry["ask"]:
            entry["ask"] = entry["header"].split(": ", 1)[-1]
        if not entry["project"]:
            prefix = entry["header"].split(" / ", 1)[0]
            entry["project"] = prefix.strip()
    return entries


def public(entry: dict) -> dict:
    """The entry shape exposed to the dashboard and the hook."""
    return {key: entry.get(key, "") for key in
            ("timestamp", "header", "project", "branch", "by", "when", "for", "state", "ask", "answer")}


def read(path: Path | None = None) -> list[dict]:
    try:
        text = file_path(path).read_text(encoding="utf-8")
    except OSError:
        return []
    return parse(text)


@contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path) + ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _format(entry: dict) -> str:
    lines = [f"- [{entry['timestamp']}] {entry['header']}",
             f"  Project: {entry['project']}", f"  Branch: {entry['branch']}",
             f"  Deferred by: {entry['by']}"]
    if entry.get("when"):
        lines.append(f"  Pick up when: {entry['when']}")
    if entry.get("for"):
        lines += [f"  For: {entry['for']}", f"  State: {entry.get('state') or 'open'}",
                  f"  Ask: {entry.get('ask') or ''}"]
        if entry.get("answer"):
            lines.append(f"  Answer: {entry['answer']}")
    return "\n".join(lines) + "\n"


def _append_entry(text: str, entry: dict) -> str:
    base = text or _TITLE
    if not base.endswith("\n"):
        base += "\n"
    if base.strip() and not base.endswith("\n\n"):
        base += "\n"
    return base + _format(entry)


def add(text: str, when: str = "", by: str = "", for_project: str = "",
        path: Path | None = None, cwd: str | os.PathLike[str] | None = None,
        now: datetime | None = None, project: str | None = None,
        branch: str | None = None) -> dict:
    """Append one deferred item while holding the file lock. ``project`` and
    ``branch`` default to the git checkout at ``cwd``."""
    text = _one_line(text, MAX_TEXT)
    when = _one_line(when, MAX_SHORT_TEXT)
    by = _one_line(by, 120) or "owner"
    for_project = _one_line(for_project, 120)
    if not text:
        raise DeferredError("nothing to add")
    target = file_path(path)
    project = _one_line(project, 120) if project else project_of(cwd)
    branch = _one_line(branch, 120) if branch else branch_of(cwd)
    entry = {"timestamp": _timestamp(now), "project": project, "branch": branch, "by": by,
             "when": when, "for": for_project, "state": "open", "ask": text, "answer": ""}
    entry["header"] = _entry_title(project, branch, text)
    with _locked(target):
        _atomic_write(target, _append_entry(_read_text(target), entry))
    return public(entry)


def _matches(entries: list[dict], header: str) -> list[dict]:
    query = _one_line(header, MAX_TEXT).casefold()
    if not query:
        return []
    exact = [entry for entry in entries if entry["header"].casefold() == query]
    return exact if exact else [entry for entry in entries if query in entry["header"].casefold()]


def _one_match(entries: list[dict], header: str) -> dict:
    matches = _matches(entries, header)
    if not matches:
        raise DeferredError(f"no deferred entry matches {header!r}")
    if len(matches) > 1:
        candidates = "\n".join(f"- {entry['header']}" for entry in matches)
        raise DeferredError(f"more than one deferred entry matches {header!r}:\n{candidates}")
    return matches[0]


def _backup(path: Path, text: str, now: datetime | None = None) -> Path:
    stamp = _timestamp(now).replace(":", "").replace("-", "")
    candidate = path.with_name(f"{path.name}.bak-{stamp}")
    index = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak-{stamp}-{index}")
        index += 1
    _atomic_write(candidate, text)
    return candidate


def _remove_block(text: str, entry: dict) -> str:
    lines = text.splitlines(keepends=True)
    start, end = int(entry["start"]), int(entry["end"])
    while end < len(lines) and not lines[end].strip():
        end += 1
    return "".join(lines[:start] + lines[end:])


def _append_log(path: Path, line: str) -> None:
    old = _read_text(path)
    _atomic_write(path, old + ("" if not old or old.endswith("\n") else "\n") + line + "\n")


def done(header: str, path: Path | None = None, removals: Path | None = None,
         now: datetime | None = None) -> dict:
    """Remove exactly one item, preserving a timestamped pre-edit backup."""
    target = file_path(path)
    with _locked(target):
        old = _read_text(target)
        entry = _one_match(parse(old), header)
        _backup(target, old, now)
        _atomic_write(target, _remove_block(old, entry))
        _append_log(removals_path(removals), f"{_timestamp(now)}\t{entry['header']}")
    return public(entry)


def _replace_field(block: list[str], label: str, value: str) -> list[str]:
    field_name = _FIELDS[label]
    for index, line in enumerate(block):
        if _field_name(line.strip()) == field_name:
            end = index + 1
            while end < len(block) and block[end][:1].isspace() and not _field_name(block[end].strip()):
                end += 1
            return block[:index] + [f"  {label}: {value}\n"] + block[end:]
    if block and not block[-1].endswith("\n"):
        block[-1] += "\n"
    return block + [f"  {label}: {value}\n"]


def answer(header: str, text: str, path: Path | None = None, now: datetime | None = None) -> dict:
    """Mark a handoff answered and attach its answer without changing its header."""
    answer_text = _one_line(text, MAX_TEXT)
    if not answer_text:
        raise DeferredError("an answer needs text")
    target = file_path(path)
    with _locked(target):
        old = _read_text(target)
        entry = _one_match(parse(old), header)
        if not entry.get("for"):
            raise DeferredError("that deferred entry is not a handoff")
        if entry.get("state") == "answered":
            raise DeferredError("that handoff is already answered")
        lines = old.splitlines(keepends=True)
        block = lines[entry["start"]:entry["end"]]
        block = _replace_field(block, "State", "answered")
        block = _replace_field(block, "Answer", answer_text)
        _atomic_write(target, "".join(lines[:entry["start"]] + block + lines[entry["end"]:]))
    entry["state"], entry["answer"] = "answered", answer_text
    return public(entry)


def _print_entries(path: Path) -> None:
    text = _read_text(path)
    if text:
        print(text, end="" if text.endswith("\n") else "\n")
    else:
        print("No deferred entries.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Park and share deferred work in Markdown.")
    sub = parser.add_subparsers(dest="command")
    add_parser = sub.add_parser("add", help="add deferred work")
    add_parser.add_argument("--by", default="owner", help="who parked it")
    add_parser.add_argument("--for", dest="for_project", default="", help="project this handoff is for")
    add_parser.add_argument("text")
    add_parser.add_argument("when", nargs="?", default="")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--done", metavar="HEADER", help="remove one matching entry")
    group.add_argument("--answer", nargs=2, metavar=("HEADER", "TEXT"), help="answer one handoff")
    group.add_argument("--list", action="store_true", help="print the Markdown backlog")
    args = parser.parse_args(argv)
    try:
        if args.command == "add":
            entry = add(args.text, args.when, args.by, args.for_project)
            print(entry["header"])
        elif args.done is not None:
            print(done(args.done)["header"])
        elif args.answer is not None:
            print(answer(args.answer[0], args.answer[1])["header"])
        elif args.list:
            _print_entries(file_path())
        else:
            parser.print_help()
            return 2
    except DeferredError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
