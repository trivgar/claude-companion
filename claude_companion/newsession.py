"""Configured, detached Claude Code session launching.

The browser selects a project name, but this module resolves that name to a
directory from the local configuration.  It never accepts a working directory
from a caller.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import config
from . import projects as _projects

MAX_PROJECTS = 40
MAX_NAME = 80
MAX_PROMPT = 12_000
VERIFY_SECONDS = 10.0
VERIFY_EVERY_SECONDS = 0.2
_TMUX_NAME = re.compile(r"[^A-Za-z0-9_-]+")
_LAUNCH_LOCK = threading.Lock()


@dataclass(frozen=True)
class Project:
    """One configured project.  ``path`` never goes back to the browser."""

    name: str
    path: Path


@dataclass(frozen=True)
class Settings:
    projects: tuple[Project, ...]
    default_model: str


@dataclass(frozen=True)
class LaunchResult:
    ok: bool
    detail: str
    session: str = ""


def settings(value=None) -> Settings:
    """The shared ``[projects]`` list plus ``[new_session].default_model``.

    ``value`` stands in for the ``[new_session]`` table alone (tests)."""
    table = config.get("new_session", {}) if value is None else value
    if not isinstance(table, dict):
        table = {}
    model = table.get("default_model")
    default_model = model.strip()[:120] if isinstance(model, str) else ""
    rows = _projects.configured() if value is None else _projects.pairs(table.get("projects"))
    projects = []
    names = set()
    for name, raw_path in rows[:MAX_PROJECTS]:
        name = name[:MAX_NAME]
        if not name or not raw_path.strip() or name.casefold() in names:
            continue
        try:
            path = Path(raw_path).expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        names.add(name.casefold())
        projects.append(Project(name, path))
    return Settings(tuple(projects), default_model)


def find_project(name: object, configured: tuple[Project, ...] | None = None) -> Project | None:
    """Find a project by its configured display name, never by a path."""
    if not isinstance(name, str):
        return None
    want = " ".join(name.split())
    for project in configured if configured is not None else settings().projects:
        if project.name == want:
            return project
    return None


def availability(project: Project) -> tuple[bool, str]:
    """Whether a configured path is usable, without exposing that path."""
    try:
        if not project.path.exists():
            return False, "Folder does not exist"
        if not project.path.is_dir():
            return False, "Path is not a folder"
    except OSError:
        return False, "Folder cannot be read"
    return True, ""


def project_rows(configured: tuple[Project, ...] | None = None) -> list[dict]:
    """The deliberately path-free data the page needs for its picker."""
    rows = []
    for project in configured if configured is not None else settings().projects:
        available, reason = availability(project)
        rows.append({"name": project.name, "available": available, "reason": reason})
    return rows


def tmux_name(project_name: str) -> str:
    """A safe tmux name that still visibly derives from the project name."""
    clean = _TMUX_NAME.sub("-", project_name.strip()).strip("-")
    return clean[:60] or "claude"


def unique_session_name(project_name: str, exists) -> str:
    """Return project, project-2, ... without overwriting an existing session."""
    base = tmux_name(project_name)
    for suffix in range(1, 101):
        candidate = base if suffix == 1 else f"{base}-{suffix}"
        if not exists(candidate):
            return candidate
    raise RuntimeError("too many sessions already use this project name")


def _tmux_exists(name: str, run) -> bool:
    try:
        result = run(["tmux", "has-session", "-t", name], capture_output=True, text=True,
                     stdin=subprocess.DEVNULL, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return False
    return getattr(result, "returncode", 1) == 0


def _pane_runs_claude(name: str, run) -> bool:
    try:
        result = run(["tmux", "display-message", "-p", "-t", name, "#{pane_current_command}"],
                     capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return False
    command = Path(str(getattr(result, "stdout", "")).strip()).name.lower()
    # An npm install of Claude Code shows as node in a pane we started with claude.
    return getattr(result, "returncode", 1) == 0 and command in {"claude", "node"}


def _tmux_command(session: str, project: Project, model: str, prompt: str) -> list[str]:
    command = ["tmux", "new-session", "-d", "-s", session, "-c", str(project.path), "claude"]
    if model:
        command.extend(["--model", model])
    if prompt:
        # A first prompt is data, even when it begins with a dash.
        command.extend(["--", prompt])
    return command


def _start(command: list[str], run, use_scope: bool) -> bool:
    args = (["systemd-run", "--user", "--scope", "--quiet", "--"] + command) if use_scope else command
    try:
        result = run(args, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return getattr(result, "returncode", 1) == 0


def launch(project: Project, model: str = "", prompt: str = "", *, run=None, which=None,
           monotonic=None, sleep=None, verify_seconds: float = VERIFY_SECONDS) -> LaunchResult:
    """Start one configured project and confirm its pane reaches ``claude``.

    A usable systemd-run puts tmux in a user scope.  If it is unavailable or
    cannot make that scope, normal detached tmux is still the reliable local
    fallback.
    """
    run = subprocess.run if run is None else run
    which = shutil.which if which is None else which
    monotonic = time.monotonic if monotonic is None else monotonic
    sleep = time.sleep if sleep is None else sleep
    available, reason = availability(project)
    if not available:
        return LaunchResult(False, reason)
    if not which("tmux"):
        return LaunchResult(False, "tmux is not installed or unavailable")
    if not which("claude"):
        return LaunchResult(False, "claude is not installed or unavailable")
    if not isinstance(prompt, str):
        return LaunchResult(False, "The first prompt must be text")
    prompt = prompt.strip()
    if len(prompt) > MAX_PROMPT:
        return LaunchResult(False, f"The first prompt is limited to {MAX_PROMPT} characters")
    # Reserve and create under one lock so two browser taps cannot claim the
    # same tmux name between has-session and new-session.
    with _LAUNCH_LOCK:
        try:
            session = unique_session_name(project.name, lambda name: _tmux_exists(name, run))
        except RuntimeError as exc:
            return LaunchResult(False, str(exc))
        command = _tmux_command(session, project, model, prompt)
        scoped = bool(which("systemd-run"))
        # A present binary can still lack a user manager.  In that case a detached
        # tmux session is preferable to refusing the configured launcher outright.
        started = _start(command, run, scoped)
        if not started and scoped:
            started = _start(command, run, False)
    if not started:
        return LaunchResult(False, "tmux could not start the session")
    deadline = monotonic() + max(0.0, verify_seconds)
    while True:
        if _pane_runs_claude(session, run):
            return LaunchResult(True, "", session)
        if monotonic() >= deadline:
            break
        sleep(min(VERIFY_EVERY_SECONDS, max(0.0, deadline - monotonic())))
    return LaunchResult(False, "Claude did not start in the new tmux pane")
