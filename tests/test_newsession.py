"""Configured new-session launching: no request can choose a folder."""
from __future__ import annotations

import stat
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent_companion import newsession  # noqa: E402
from agent_companion import server  # noqa: E402
from agent_companion.server import newsession as routes  # noqa: E402

TOKEN = "t" * 32
OWNER = "o" * 64
KEY = {"x-agent-companion-key": OWNER}
SAME = {"origin": "http://testserver", "host": "testserver"}
WRITE = {**KEY, **SAME}


def _write_config(path: Path, project: Path, missing: Path) -> None:
    path.write_text(
        "[new_session]\n"
        'default_model = "sonnet"\n'
        f'projects = [{{ name = "alpha", path = "{project}" }}, '
        f'{{ name = "missing", path = "{missing}" }}]\n'
    )


def test_config_table_has_only_valid_unique_configured_projects(tmp_path, monkeypatch):
    folder = tmp_path / "alpha"
    folder.mkdir()
    config_file = tmp_path / "config.toml"
    _write_config(config_file, folder, tmp_path / "gone")
    monkeypatch.setenv("AGENT_COMPANION_CONFIG", str(config_file))

    configured = newsession.settings()

    assert configured.default_model == "sonnet"
    assert [(item.name, item.path) for item in configured.projects] == [
        ("alpha", folder.resolve()), ("missing", (tmp_path / "gone").resolve())]
    assert newsession.project_rows(configured.projects) == [
        {"name": "alpha", "available": True, "reason": ""},
        {"name": "missing", "available": False, "reason": "Folder does not exist"},
    ]


def test_unique_session_name_adds_a_suffix_without_reusing_a_tmux_name():
    taken = {"alpha", "alpha-2"}
    assert newsession.unique_session_name("alpha", taken.__contains__) == "alpha-3"


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_launch_checks_a_fake_tmux_pane_with_claude_on_path(tmp_path, monkeypatch):
    project = tmp_path / "alpha"
    project.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _executable(bin_dir / "claude", "#!/bin/sh\nexit 0\n")
    _executable(bin_dir / "tmux", """#!/bin/sh
case "$1" in
  has-session) exit 1 ;;
  new-session) exit 0 ;;
  display-message) printf 'claude\\n'; exit 0 ;;
esac
exit 1
""")
    monkeypatch.setenv("PATH", str(bin_dir))

    result = newsession.launch(newsession.Project("alpha", project), model="sonnet", prompt="begin")

    assert result == newsession.LaunchResult(True, "", "alpha")


@pytest.fixture
def client(tmp_path, monkeypatch):
    folder = tmp_path / "alpha"
    folder.mkdir()
    config_file = tmp_path / "config.toml"
    _write_config(config_file, folder, tmp_path / "gone")
    monkeypatch.setenv("AGENT_COMPANION_CONFIG", str(config_file))
    monkeypatch.setattr(server, "FEATURES", [])
    return TestClient(server.create_app(token=TOKEN, owner_key=OWNER))


def test_routes_refuse_unknown_projects_and_enforce_owner_write_rules(client, monkeypatch):
    started = []
    monkeypatch.setattr(routes.newsession, "launch", lambda *args: started.append(args) or newsession.LaunchResult(True, "", "alpha"))

    assert client.get("/api/new-session/projects").status_code == 401
    assert client.get("/api/new-session/projects", headers={"x-agent-companion-token": TOKEN}).status_code == 401
    assert client.post("/api/new-session", json={"project": "alpha"}, headers=SAME).status_code == 401
    assert client.post("/api/new-session", json={"project": "alpha"}, headers={**KEY, "origin": "http://other.example", "host": "testserver"}).status_code == 403
    refused = client.post("/api/new-session", json={"project": str(Path("/not-configured"))}, headers=WRITE)
    assert refused.status_code == 400 and refused.json()["detail"] == "unknown configured project"
    assert not started

    rows = client.get("/api/new-session/projects", headers=KEY).json()
    assert rows == {"projects": [
        {"name": "alpha", "available": True, "reason": ""},
        {"name": "missing", "available": False, "reason": "Folder does not exist"},
    ], "default_model": "sonnet"}
    assert all("path" not in row for row in rows["projects"])

    started_ok = client.post("/api/new-session", json={"project": "alpha", "prompt": "begin"}, headers=WRITE)
    assert started_ok.status_code == 200 and started_ok.json() == {"ok": True, "session": "alpha"}
    assert started[0][0].name == "alpha" and started[0][1:] == ("sonnet", "begin")
