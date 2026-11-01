"""Settings: AGENT_COMPANION_<KEY> in the environment first, then
~/.config/agent-companion/config.toml. A file, not only the environment,
because Claude Code's hooks import this outside the server's process."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path


def path() -> Path:
    return Path(os.environ.get("AGENT_COMPANION_CONFIG",
                               Path.home() / ".config" / "agent-companion" / "config.toml"))


def get(key: str, default=None):
    env = os.environ.get("AGENT_COMPANION_" + key.upper())
    if env is not None:
        return env
    try:
        return tomllib.loads(path().read_text()).get(key, default)
    except (OSError, ValueError):
        return default


def problem() -> str:
    """Why the config file cannot be used, or "" when it is fine or absent.
    A broken file otherwise reads as every setting at its default, silently."""
    try:
        tomllib.loads(path().read_text())
    except OSError:
        return ""
    except ValueError as exc:
        return f"{path()} is not valid TOML ({exc}); every setting is at its default"
    return ""


def paths(key: str) -> tuple[Path, ...]:
    """A list in the file, or os.pathsep-separated in the environment."""
    v = get(key) or ()
    if isinstance(v, str):
        v = v.split(os.pathsep)
    return tuple(Path(p) for p in v if isinstance(p, str) and p)
