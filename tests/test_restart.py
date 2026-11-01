import os

import pytest

pytest.importorskip("fastapi")

from claude_companion import restart, server  # noqa: E402
from claude_companion.server import auth  # noqa: E402
from routes_walk import all_routes  # noqa: E402


class Result:
    def __init__(self, value, returncode=0):
        self.stdout = value + "\n"
        self.returncode = returncode


def _package(tmp_path):
    package = tmp_path / "claude_companion"
    package.mkdir()
    (package / "module.py").write_text("x = 1\n")
    return package


def test_a_changed_checkout_requests_a_restart(tmp_path):
    package = _package(tmp_path)
    values = iter(["a" * 40, "b" * 40])
    check = restart.Check(package=package, repo=tmp_path, now=100,
                          run=lambda *args, **kwargs: Result(next(values)))
    assert check.needed()


def test_an_unchanged_checkout_does_not_request_a_restart(tmp_path):
    package = _package(tmp_path)
    check = restart.Check(package=package, repo=tmp_path, now=100,
                          run=lambda *args, **kwargs: Result("a" * 40))
    assert not check.needed()


def test_installs_without_git_fall_back_to_newer_source_files(tmp_path):
    package = _package(tmp_path)
    os.utime(package / "module.py", (99, 99))
    check = restart.Check(package=package, repo=tmp_path, now=100,
                          run=lambda *args, **kwargs: Result("", returncode=1))
    assert not check.needed()
    os.utime(package / "module.py", (101, 101))
    assert check.needed()


def test_the_dashboard_reports_restart_state_and_documented_command(monkeypatch):
    class Changed:
        def needed(self):
            return True

    monkeypatch.setattr(server.restart, "Check", Changed)
    monkeypatch.setattr(server, "FEATURES", [])
    app = server.create_app(token="t" * 32, owner_key="o" * 64)
    route = next(route for route in all_routes(app) if getattr(route, "path", "") == "/api/needs-restart")
    assert route.dependant.dependencies[0].call is auth.require_token
    assert route.endpoint() == {
        "needed": True, "command": restart.RESTART_COMMAND}


def test_the_header_has_the_restart_message_and_command_slot():
    page = (server.STATIC / "index.html").read_text()
    script = (server.STATIC / "app.js").read_text()
    assert "Restart to load the new code" in page and 'id="needs-restart"' in page
    assert "/api/needs-restart" in script
