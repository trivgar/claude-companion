"""Every script a person runs by path is executable in git. The repo is often
edited from a Windows drive, where git cannot see the bit (core.fileMode false),
so a fresh clone got "Permission denied" from comms/install.sh."""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SOURCED = {"comms/_env.sh"}   # sourced, never run


def test_scripts_are_executable_in_git():
    try:
        out = subprocess.run(["git", "ls-files", "-s"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    bad = []
    for line in out.splitlines():
        mode, _, _, path = line.split(maxsplit=3)
        if path in SOURCED or mode != "100644":
            continue
        if path.endswith(".sh") or (ROOT / path).read_bytes()[:2] == b"#!":
            bad.append(path)
    assert not bad, f"chmod +x in git (git update-index --chmod=+x): {bad}"
