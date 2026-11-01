"""Messages agents send each other, pulled out of the transcripts.

Sessions on this machine talk to one another with the SendMessage tool, and
none of that traffic reaches you. One busy day saw a session send 18 of them
and another 26, and a dashboard that only shows what was said aloud showed
none. This is the other channel.

WHAT A SEND LOOKS LIKE. The sender's transcript carries a tool_use:

    {"type":"tool_use","id":"toolu_...","name":"SendMessage",
     "input":{"to":"home-assistant-d7","message":"<the body>"}}

and, further down the same file, its result:

    {"type":"tool_result","tool_use_id":"toolu_...",
     "content":"{\\"success\\":true,\\"message\\":\\"\\u201cSubject\\u201d \\u2192 target ...\\"}"}

The result is worth having for two reasons: it carries a short subject the
sender wrote, which is a far better one-line summary than the first 60
characters of the body, and it says whether the message actually arrived. A
send that failed is the one most worth seeing, so it is kept and marked rather
than dropped.

A tool_use and its result can land in different reads, so pending sends are
held per file across refreshes and patched when the result turns up.

NEVER RE-READ A WHOLE TRANSCRIPT. Same watermark as UsageIndex: per-file byte
offset plus inode, reset if the file shrank or the inode changed, persisted so
a restart re-reads nothing. Only top-level `*/*.jsonl` is scanned -- these are
messages between the MAIN agents; subagents do not hold conversations.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from agent_companion.usage import _parse_ts       # one date parser, not two that drift

CACHE_NAME = "session-companion-messages.json"
KEEP = 7 * 24 * 3600              # a week, matching the widest usage window
PER_FILE_MAX = 200                # a runaway sender cannot crowd out the rest

# The result reads: “Subject line” → target (another Claude session ...)
_SUBJECT = re.compile(r"[“\"]([^”\"]{1,200})[”\"]")


def _text_of(content) -> str:
    """tool_result content is a string, or a list of {type,text} blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and isinstance(b.get("text"), str))
    return ""


def project_from(cwd: str | None, directory: str) -> str:
    """Prefer the transcript's own cwd. The encoded directory name is lossy --
    every slash became a dash, and project names contain dashes too, so
    `-home-user-my-project` cannot be split back apart. Its tail
    is only a guess, used for transcripts old enough to predate the cwd field.
    """
    if cwd:
        return Path(cwd).name or cwd
    return directory.rsplit("-", 1)[-1] or directory


def _folder(path) -> str:
    """A cwd as Claude Code names its transcript folder: every character but
    a letter or digit becomes "-" ("/home/t/.x" is "-home-t--x"). A prefix
    match on it also hides subfolders, and over-hides a sibling sharing the
    prefix, which is the safe way to be wrong."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path).rstrip("/"))


class MessageIndex:
    """Recent inter-agent messages across every transcript. Create once, reuse."""

    def __init__(self, home: Path, cache_path: Path | None = None, hide=None) -> None:
        # () -> folders whose transcripts are someone else's: never scanned.
        self._hide = hide
        self.home = Path(home)
        self.cache_path = Path(cache_path) if cache_path else self.home / CACHE_NAME
        # path -> {"offset", "inode", "cwd", "branch", "msgs": {tool_id: msg}}
        self._files: dict[str, dict] = {}
        self._load()

    # -- persistence ------------------------------------------------------
    def _load(self) -> None:
        try:
            data = json.loads(self.cache_path.read_text())
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        for path, entry in (data.get("files") or {}).items():
            if not isinstance(entry, dict):
                continue
            msgs = entry.get("msgs")
            if not isinstance(msgs, dict):
                continue
            self._files[path] = {
                "offset": int(entry.get("offset") or 0),
                "inode": int(entry.get("inode") or 0),
                "cwd": entry.get("cwd") or "",
                "branch": entry.get("branch") or "",
                "msgs": {k: v for k, v in msgs.items() if isinstance(v, dict)},
            }

    def _save(self) -> None:
        payload = {"files": {p: {"offset": e["offset"], "inode": e["inode"],
                                 "cwd": e["cwd"], "branch": e["branch"],
                                 "msgs": e["msgs"]}
                             for p, e in self._files.items()}}
        tmp = self.cache_path.with_suffix(".json.sc-tmp")
        try:
            tmp.write_text(json.dumps(payload))
            os.replace(tmp, self.cache_path)
        except OSError:
            pass          # a cache we cannot write is a slow poll, not a failure

    # -- scanning ---------------------------------------------------------
    def _prune(self, now: float) -> None:
        cutoff = now - KEEP
        for entry in self._files.values():
            msgs = entry["msgs"]
            for k in [k for k, m in msgs.items() if (m.get("at") or 0) < cutoff]:
                del msgs[k]
            if len(msgs) > PER_FILE_MAX:
                keep = sorted(msgs.items(), key=lambda kv: kv[1].get("at") or 0,
                              reverse=True)[:PER_FILE_MAX]
                entry["msgs"] = dict(keep)

    def _consume(self, entry: dict, chunk: bytes) -> None:
        for raw in chunk.decode("utf-8", "replace").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                continue          # one bad line must not cost us the file
            if not isinstance(rec, dict):
                continue
            # Recorded once per file: every record carries the same values.
            if not entry["cwd"] and isinstance(rec.get("cwd"), str):
                entry["cwd"] = rec["cwd"]
            if not entry["branch"] and isinstance(rec.get("gitBranch"), str):
                entry["branch"] = rec["gitBranch"]

            content = (rec.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                kind = block.get("type")
                if kind == "tool_use" and block.get("name") == "SendMessage":
                    tid = block.get("id")
                    inp = block.get("input")
                    if not isinstance(tid, str) or not isinstance(inp, dict):
                        continue
                    entry["msgs"][tid] = {
                        "at": _parse_ts(rec.get("timestamp")),
                        "to": str(inp.get("to") or "")[:120],
                        "body": str(inp.get("message") or ""),
                        "subject": None,
                        "ok": None,
                        "session": rec.get("sessionId") or "",
                    }
                elif kind == "tool_result":
                    tid = block.get("tool_use_id")
                    msg = entry["msgs"].get(tid) if isinstance(tid, str) else None
                    if msg is None:
                        continue
                    text = _text_of(block.get("content"))
                    try:
                        parsed = json.loads(text)
                    except ValueError:
                        parsed = None
                    if isinstance(parsed, dict):
                        msg["ok"] = bool(parsed.get("success"))
                        found = _SUBJECT.search(str(parsed.get("message") or ""))
                    else:
                        msg["ok"] = block.get("is_error") is not True
                        found = _SUBJECT.search(text)
                    if found:
                        msg["subject"] = found.group(1)

    def refresh(self, now: float | None = None) -> None:
        """Read whatever is new. Cheap enough to call on every poll."""
        now = now or time.time()
        projects = self.home / "projects"
        if not projects.is_dir():
            return

        touched = False
        hidden = [_folder(h) for h in (self._hide() if self._hide else []) if str(h).strip("/")]

        def is_hidden(name: str) -> bool:
            return any(name == h or name.startswith(h + "-") for h in hidden)

        for key in [k for k in self._files if is_hidden(Path(k).parent.name)]:
            del self._files[key]
            touched = True
        for path in projects.glob("*/*.jsonl"):
            if is_hidden(path.parent.name):
                continue
            key = str(path)
            try:
                st = path.stat()
            except OSError:
                continue
            if st.st_mtime < now - KEEP and key not in self._files:
                continue

            entry = self._files.setdefault(
                key, {"offset": 0, "inode": st.st_ino, "cwd": "", "branch": "", "msgs": {}})
            if st.st_ino != entry["inode"] or st.st_size < entry["offset"]:
                # Rotated or truncated: drop what this file contributed, or the
                # re-read would add to its own history instead of replacing it.
                entry.update(offset=0, inode=st.st_ino, cwd="", branch="", msgs={})
            if st.st_size <= entry["offset"]:
                continue

            try:
                with path.open("rb") as fh:
                    fh.seek(entry["offset"])
                    chunk = fh.read(st.st_size - entry["offset"])
            except OSError:
                continue
            # Stop at the last complete line; a half-written record is not a
            # parse failure to swallow, it is bytes that have not arrived yet.
            cut = chunk.rfind(b"\n")
            if cut < 0:
                continue
            self._consume(entry, chunk[:cut + 1])
            entry["offset"] += cut + 1
            touched = True

        self._prune(now)
        if touched:
            self._save()

    def recent(self, limit: int = 50) -> list[dict]:
        """Newest first, across every session."""
        out: list[dict] = []
        for key, entry in self._files.items():
            project = project_from(entry.get("cwd"), Path(key).parent.name)
            for msg in entry["msgs"].values():
                out.append({**msg,
                            "from_project": project,
                            "from_branch": entry.get("branch") or ""})
        out.sort(key=lambda m: m.get("at") or 0, reverse=True)
        return out[:max(1, limit)]
