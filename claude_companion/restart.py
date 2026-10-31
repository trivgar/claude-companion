"""Tell a running dashboard when its checkout has changed underneath it."""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

RESTART_COMMAND = "systemctl --user restart claude-companion"


def package_root() -> Path:
    return Path(__file__).resolve().parent


def repo_root(package: Path) -> Path | None:
    """The checkout containing this installed package, when there is one."""
    for candidate in (package.parent, *package.parent.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def git_head(repo: Path | None, run=subprocess.run) -> str:
    """The checkout's commit, or an empty string when Git is unavailable."""
    if repo is None:
        return ""
    try:
        result = run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                     capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return ""
    value = result.stdout.strip() if result.returncode == 0 else ""
    return value if re.fullmatch(r"[0-9a-fA-F]{7,64}", value) else ""


def source_changed(package: Path, started_at: float) -> bool:
    """Fallback for installs without Git: Python newer than this process."""
    try:
        return any(path.stat().st_mtime > started_at for path in package.rglob("*.py"))
    except OSError:
        return False


class Check:
    """A start-time code snapshot that can be compared without server state."""

    def __init__(self, package: Path | None = None, repo: Path | None = None,
                 now: float | None = None, run=subprocess.run) -> None:
        self.package = package or package_root()
        self.repo = repo if repo is not None else repo_root(self.package)
        self.started_at = time.time() if now is None else now
        self.run = run
        self.started_head = git_head(self.repo, run)

    def needed(self) -> bool:
        current = git_head(self.repo, self.run)
        if self.started_head and current:
            return current != self.started_head
        return source_changed(self.package, self.started_at)
