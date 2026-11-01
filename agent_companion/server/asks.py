"""Asks: an agent's request for permission, or its multiple-choice question,
answered from this page.

A hook (agent_companion/hooks/permission_hook.py) posts each request here,
pushes the owner a notification, and waits for the answer; the Asks tab shows
it with Allow and Deny, or the question's options. No answer in time and the
session's own dialog appears as before.

One JSON file per ask under ~/.local/share/agent-companion/asks/ (files 600,
folder 700): what was asked, by whom, and the answer. Pure but for the files.

An ask is live only while its hook is still waiting on it, and the file's
mtime says so: the hook asks after its ask every 1.5 s and each time is
heard(). An ask its hook has stopped asking after is "stopped": off the screen,
and an answer to it is refused with the reason rather than taken and lost.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import time
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import addons, config
from . import auth

TITLE = "Asks"

EXPIRE_S = 25 * 3600          # past the longest a hook waits
KEEP_S = 48 * 3600
LIVE_S = 30                    # its hook asks every 1.5 s; this long unheard and it has gone
DECISIONS = ("allow", "deny")


def store() -> Path:
    raw = (config.env("ASKS") or config.get("asks_dir")
           or Path.home() / ".local" / "share" / "agent-companion" / "asks")
    return Path(str(raw)).expanduser()


FULL_CAP = 20 * 1024            # bytes of a tool's whole input kept for "show all"

# What summary() shows per tool, and the fields that change nothing a person
# would judge. Any other field is "left out" and makes the summary shortened.
_SHOWN = {"Bash": ("command",), "Edit": ("file_path",), "Write": ("file_path",), "MultiEdit": ("file_path",),
          "NotebookEdit": ("notebook_path", "file_path"), "Read": ("file_path", "notebook_path"),
          "WebFetch": ("url",), "WebSearch": ("query",)}
_BENIGN = {"Bash": ("description", "timeout", "run_in_background"), "Read": ("offset", "limit"),
           "WebFetch": ("prompt",)}


def _summarise(tool: str, inp: dict | None) -> tuple[str, bool]:
    """(one line to judge, whether it hides anything). Shortened is true when
    the line was cut, a field was cut or dropped, or the tool carried fields
    the line does not show."""
    inp = inp if isinstance(inp, dict) else {}
    short = False
    if tool in _SHOWN:
        shown = _SHOWN[tool]
        what = next((inp[k] for k in shown if inp.get(k)), "")
        left_out = [k for k in inp if k not in shown and k not in _BENIGN.get(tool, ()) and inp[k] not in (None, "", [], {})]
        short = bool(left_out)
    else:
        items = list(inp.items())
        what = ", ".join(f"{k}: {str(v)[:60]}" for k, v in items[:3])
        short = len(items) > 3 or any(len(str(v)) > 60 for _, v in items[:3])
    raw = str(what)
    line = " ".join(raw.split())
    # The one line must be exactly what runs, in plain printable ASCII. A
    # newline, a tab run, or any non-ASCII character (invisible, bidi,
    # look-alike) means the full view has to be opened before Allow.
    if len(line) > 400 or line != raw or not all(" " <= c <= "~" for c in raw):
        short = True
    return line[:400], short


def summary(tool: str, inp: dict | None) -> str:
    """What the agent wants to do, in one line to judge."""
    return _summarise(tool, inp)[0]


def full_input(inp: dict | None) -> tuple[str, bool]:
    """The whole tool input as plain text (a field per block, strings as they
    are), cut at FULL_CAP bytes. (text, was_cut)."""
    inp = inp if isinstance(inp, dict) else {}
    parts = []
    for k, v in inp.items():
        body = v if isinstance(v, str) else json.dumps(v, indent=2, ensure_ascii=False, default=str)
        parts.append(f"{k}:\n{body}")
    raw = "\n\n".join(parts).encode()
    if len(raw) <= FULL_CAP:
        return raw.decode(), False
    return raw[:FULL_CAP].decode("utf-8", "ignore"), True


def _dir(root: Path | None = None) -> Path:
    d = Path(root or store())
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def _write(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def clean_questions(qs) -> list[dict]:
    """An agent's multiple-choice questions (AskUserQuestion), kept to the
    shape the page draws: question, header, multiSelect, options' label and
    description. Anything else is dropped."""
    out = []
    for q in (qs if isinstance(qs, list) else [])[:4]:
        if not isinstance(q, dict) or not str(q.get("question") or "").strip():
            continue
        opts = [{"label": str(o.get("label") or "")[:80], "description": str(o.get("description") or "")[:300]}
                for o in (q.get("options") if isinstance(q.get("options"), list) else [])[:6]
                if isinstance(o, dict) and str(o.get("label") or "").strip()]
        out.append({"question": str(q["question"])[:500], "header": str(q.get("header") or "")[:40],
                    "multiSelect": bool(q.get("multiSelect")), "options": opts})
    return out


def create(speaker: str, project: str, session_id: str, tool: str, inp: dict | None,
           root: Path | None = None, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    questions = clean_questions((inp or {}).get("questions")) if tool == "AskUserQuestion" else []
    what, short = (questions[0]["question"], False) if questions else _summarise(tool, inp)
    a = {"id": secrets.token_hex(8), "at": now, "speaker": str(speaker or "")[:40],
         "project": str(project or "")[:80], "session_id": str(session_id or "")[:80],
         "tool": str(tool or "")[:60],
         "what": what, "status": "pending"}
    if questions:
        a["questions"] = questions
    else:
        a["input_full"], cut = full_input(inp)
        a["input_truncated"] = cut
        a["shortened"] = bool(short or cut)
    _write(_dir(root) / f"{a['id']}.json", a)
    return a


def _valid(ask_id: str) -> bool:
    return bool(ask_id) and all(c in "0123456789abcdef" for c in ask_id)


def _unheard(path: Path, a: dict, now: float) -> bool:
    """Its hook has not asked after it for LIVE_S: timed out, interrupted, or
    its session ended. Counted from the later of its creation and the last
    time the hook was heard (the file's mtime)."""
    try:
        heard_at = path.stat().st_mtime
    except OSError:
        return True
    return now - max(heard_at, float(a.get("at") or 0)) > LIVE_S


def heard(ask_id: str, root: Path | None = None) -> None:
    """Its hook asked after it: still waiting."""
    if _valid(ask_id):
        try:
            os.utime(_dir(root) / f"{ask_id}.json")
        except OSError:
            pass


def get(ask_id: str, root: Path | None = None, now: float | None = None) -> dict | None:
    if not _valid(ask_id):
        return None
    path = _dir(root) / f"{ask_id}.json"
    try:
        a = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    now = time.time() if now is None else now
    if a.get("status") == "pending" and now - float(a.get("at") or 0) > EXPIRE_S:
        a["status"] = "expired"
    elif a.get("status") == "pending" and _unheard(path, a, now):
        a["status"] = "stopped"
    return a


def pending(root: Path | None = None, now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    out = []
    for f in _dir(root).glob("*.json"):
        try:
            a = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        age = now - float(a.get("at") or 0)
        if age > KEEP_S:
            f.unlink(missing_ok=True)
            continue
        if a.get("status") == "pending" and age <= EXPIRE_S and not _unheard(f, a, now):
            out.append(a)
    return sorted(out, key=lambda a: a["at"])


def decide(ask_id: str, decision: str, root: Path | None = None, now: float | None = None,
           reviewed: bool = True) -> dict | None:
    """The owner's answer. None when there is no such pending ask (answered
    already, expired, stopped, or never asked). reviewed: the owner opened the
    full input; an Allow of a shortened ask needs it."""
    if decision not in DECISIONS:
        return None
    a = get(ask_id, root, now)
    if not a or a.get("status") != "pending" or a.get("questions"):
        return None               # a question is answered, never allowed: its hook waits for answers
    if decision == "allow" and a.get("shortened") and not reviewed:
        return None
    if decision == "allow" and a.get("input_truncated"):
        return None               # too long to show whole here: allowed only in the terminal
    a["status"], a["decided_at"] = decision, time.time() if now is None else now
    _write(_dir(root) / f"{ask_id}.json", a)
    return a


def answer(ask_id: str, answers: dict, root: Path | None = None, now: float | None = None) -> dict | None:
    """The owner's answers to an agent's questions: {question: label, ...},
    one for every question asked and only those; several picks are joined with
    ", " as the terminal's own dialog does. None when it is not a pending
    question or the answers do not fit it."""
    a = get(ask_id, root, now)
    if not a or a.get("status") != "pending" or not a.get("questions") or not isinstance(answers, dict):
        return None
    asked = [q["question"] for q in a["questions"]]
    got = {str(k): " ".join(str(v).split())[:2000] for k, v in answers.items()}
    if set(got) != set(asked) or not all(got.values()):
        return None
    a["status"], a["answers"], a["decided_at"] = "answered", got, time.time() if now is None else now
    _write(_dir(root) / f"{ask_id}.json", a)
    return a


def withdraw(ask_id: str, root: Path | None = None, now: float | None = None) -> None:
    """The session stopped waiting (it timed out, or was answered in its own
    window): take it off the screen."""
    a = get(ask_id, root, now)
    if a and a.get("status") == "pending":
        a["status"] = "withdrawn"
        _write(_dir(root) / f"{ask_id}.json", a)


# WHOSE ASK IT IS. An ask belongs to the row with its session_id, falling
# back to its speaker only when that session has gone. A helper's ask is
# lifted onto its agent's row because helpers do not have cards of their own.

CARD_KEYS = ("id", "tool", "what", "at", "questions", "shortened", "input_full")


def is_helper(r: dict) -> bool:
    return bool(r.get("child")) and not r.get("job") and bool(r.get("root_pid"))


def from_label(label) -> str:
    """Which helper, in words: its label, or "" when it has none worth
    saying ("subagent" is the core's fallback word, not a description)."""
    label = str(label or "").strip()
    return "" if label in ("", "subagent") else label


def owner(a: dict, rows: list[dict]) -> dict | None:
    """The row an ask belongs to: by session_id, else by speaker."""
    sid = a.get("session_id")
    if sid:
        for r in rows:
            if r.get("session_id") == sid:
                return r
    who = a.get("speaker")
    if who:
        for r in rows:
            if r.get("speaker") == who:
                return r
    return None


def _root(r: dict, rows: list[dict]) -> dict | None:
    if not is_helper(r):
        return None
    return next((x for x in rows if x.get("pid") == r.get("root_pid") and x is not r), None)


def attach(rows: list[dict], pending_asks: list[dict]) -> None:
    """Each row's newest pending ask as r["ask"], then each helper's lifted
    onto its agent with `from` and waiting: the older helper first, and never
    over the agent's own ask, which always wins. A lifted ask keeps the
    agent's own waiting as `own_waiting`: `waiting` puts it in Needs you, but
    only its own says its pane is at a dialog, which is what hides its compact
    and clear."""
    mine: dict[int, list[dict]] = {}
    for a in pending_asks:
        r = owner(a, rows)
        if r is not None:
            mine.setdefault(id(r), []).append(a)
    for r in rows:
        got = mine.get(id(r))
        if got:
            r["ask"] = {k: got[-1].get(k) for k in CARD_KEYS if got[-1].get(k)}
    lifts = []
    for r in rows:
        root = _root(r, rows) if r.get("ask") else None
        if root is not None:
            lifts.append((float(r["ask"].get("at") or 0), r, root))
    for _, r, root in sorted(lifts, key=lambda x: x[0]):
        if root.get("ask"):
            continue
        root["ask"] = dict(r["ask"], **{"from": from_label(r.get("label"))})
        root["own_waiting"] = bool(root.get("waiting"))
        root["waiting"] = True


def resolve(a: dict, rows: list[dict]) -> dict:
    """{"from", "root"} for a helper's ask whose agent has a speaker, so a
    push or a "Waiting on you" row can name and open the agent; {} for any
    other ask."""
    r = owner(a, rows)
    root = _root(r, rows) if r is not None else None
    if root is None or not root.get("speaker"):
        return {}
    return {"from": from_label(r.get("label")), "root": root["speaker"]}


def for_agent(pending_asks: list[dict], rows: list[dict], pid) -> list[dict]:
    """An agent's chat window's asks: its own, and its helpers' with `from`."""
    out = []
    for a in pending_asks:
        r = owner(a, rows)
        if r is None:
            continue
        if r.get("pid") == pid:
            out.append(a)
        elif is_helper(r) and r.get("root_pid") == pid:
            out.append(dict(a, **{"from": from_label(r.get("label"))}))
    return out


def not_waiting(ask_id: str) -> str:
    """Why an answer could not be taken, when it is that the agent stopped
    waiting here: said plainly, so a tap never looks like it went somewhere."""
    a = get(ask_id) or {}
    who = a.get("speaker") or "The agent"
    if a.get("status") in ("stopped", "expired", "withdrawn"):
        return f"{who} stopped waiting here: if it is still asking, its own window has it"
    if a.get("status") in ("answered", "allow", "deny"):
        return "already answered"
    return ""


def push_text(a: dict) -> tuple[str, str]:
    """(speaker, line) for the notification a new ask sends."""
    if a.get("questions"):
        q = a["questions"][0]
        opts = " · ".join(o["label"] for o in q["options"])
        more = len(a["questions"]) - 1
        line = (f"{q['question']}\n{opts}" if opts else q["question"]) + (
            f"\n(+{more} more question{'s' if more > 1 else ''})" if more else "")
        return a["speaker"], line[:300]
    return a["speaker"], f"{a['tool']}: {a['what']}"[:300]


def _push(a: dict) -> None:
    """Never raises, never under pytest."""
    if "pytest" in sys.modules:
        return
    try:
        from agent_companion import notify
        speaker, line = push_text(a)
        notify.notify(line, speaker=speaker)
    except Exception:
        pass


# ROUTES. The hook runs on this machine: create, poll and withdraw take the
# HOOK token (it reads the token file) and refuse anything that came through a
# proxy. The owner's routes below take an owner key and never the hook token.

router = APIRouter()


def _local_only(request: Request) -> None:
    if request.headers.get("x-forwarded-for") or request.headers.get("x-forwarded-host"):
        raise HTTPException(403, "this machine only")


@router.post("/api/asks", dependencies=[Depends(auth.require_hook)])
def asks_create(request: Request, body: dict = Body(default={})):
    _local_only(request)
    inp = body.get("input") if isinstance(body.get("input"), dict) else {}
    a = create(body.get("speaker") or "", body.get("project") or "", body.get("session_id") or "",
               body.get("tool") or "", inp)
    result = addons.on_ask("created", a)
    if not (isinstance(result, dict) and result.get("pushed") is True):
        _push(a)
    out = {"ok": True, "id": a["id"]}
    if isinstance(result, dict) and result.get("answerable") is False:
        out["answerable"] = False
    return out


@router.get("/api/asks/{ask_id}", dependencies=[Depends(auth.require_hook)])
def asks_one(request: Request, ask_id: str):
    _local_only(request)
    heard(ask_id)                          # its hook, asking after it: still waiting
    a = get(ask_id)
    out = {"ok": bool(a), "status": (a or {}).get("status", "gone")}
    if a and a.get("answers"):
        out["answers"] = a["answers"]
    return out


@router.post("/api/asks/{ask_id}/withdraw", dependencies=[Depends(auth.require_hook)])
def asks_withdraw(request: Request, ask_id: str):
    _local_only(request)
    withdraw(ask_id)
    addons.on_ask("withdrawn", {"id": ask_id})
    return {"ok": True}


@router.get("/api/asks", dependencies=[Depends(auth.require_token)])
def asks_list():
    try:
        from . import sessions
        rows = sessions.order(sessions._parts()["reader"].snapshot())
    except Exception:
        rows = []
    return JSONResponse({"asks": [dict(a, **resolve(a, rows)) for a in pending()]},
                        headers={"Cache-Control": "no-store"})


@router.post("/api/asks/{ask_id}/decide", dependencies=[Depends(auth.require_write)])
def asks_decide(ask_id: str, body: dict = Body(default={})):
    cur = get(ask_id) or {}
    if (body.get("decision") == "allow" and cur.get("status") == "pending" and cur.get("shortened")
            and body.get("reviewed") is not True):
        raise HTTPException(400, "that request is shortened here: open show all, read it, then allow")
    if body.get("decision") == "allow" and cur.get("status") == "pending" and cur.get("input_truncated"):
        raise HTTPException(400, "that request is too long to show whole here: deny it, or allow it in the terminal")
    a = decide(ask_id, str(body.get("decision") or ""), reviewed=body.get("reviewed") is True)
    if not a:
        raise HTTPException(409, not_waiting(ask_id) or (
            "a question is answered with its options, not allowed or denied"
            if (get(ask_id) or {}).get("questions") else "that ask is no longer waiting"))
    addons.on_ask("decided", a)
    return {"ok": True, "status": a["status"]}


@router.post("/api/asks/{ask_id}/answer", dependencies=[Depends(auth.require_write)])
def asks_answer(ask_id: str, body: dict = Body(default={})):
    a = answer(ask_id, body.get("answers") if isinstance(body.get("answers"), dict) else {})
    if not a:
        raise HTTPException(409, not_waiting(ask_id)
                            or "that question is no longer waiting, or the answers do not fit it")
    addons.on_ask("answered", a)
    return {"ok": True, "status": a["status"]}
