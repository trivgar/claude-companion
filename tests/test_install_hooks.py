import json

import pytest

pytest.importorskip("fastapi")
from agent_companion.server import install_hooks as ih  # noqa: E402

OTHER = {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 /x/other.py"}]}


def _settings(tmp_path, data):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(data))
    return p


def test_it_adds_hooks_after_a_backup_and_keeps_the_rest(tmp_path):
    p = _settings(tmp_path, {"model": "x", "hooks": {"PreToolUse": [OTHER]}})
    ih.run(p, python="/py")
    got = json.loads(p.read_text())
    assert got["model"] == "x" and got["hooks"]["PreToolUse"][0] == OTHER
    cmd = "/py -m agent_companion.hooks.permission_hook"
    assert got["hooks"]["PreToolUse"][1]["matcher"] == "AskUserQuestion"
    assert got["hooks"]["PreToolUse"][1]["hooks"][0]["command"] == cmd
    assert got["hooks"]["PermissionRequest"][0]["hooks"][0]["command"] == cmd
    commit = got["hooks"]["PostToolUse"][0]
    assert commit["matcher"] == "Bash"
    assert commit["hooks"][0]["command"] == "/py -m agent_companion.hooks.commit_hook"
    for event in ("SessionStart", "UserPromptSubmit"):
        hook = got["hooks"][event][0]
        assert hook["matcher"] == ".*"
        assert hook["hooks"][0]["command"] == "/py -m agent_companion.hooks.deferred_hook"
    baks = list(tmp_path.glob("settings.json.bak-*"))
    assert len(baks) == 1 and json.loads(baks[0].read_text())["model"] == "x"


def test_a_second_run_changes_nothing_and_makes_no_backup(tmp_path):
    p = _settings(tmp_path, {})
    ih.run(p, python="/py")
    first = p.read_text()
    for f in tmp_path.glob("*.bak-*"):
        f.unlink()
    assert "nothing changed" in ih.run(p, python="/py")
    assert p.read_text() == first and not list(tmp_path.glob("*.bak-*"))


def test_a_moved_python_replaces_our_entry_not_adds_a_second(tmp_path):
    p = _settings(tmp_path, {})
    ih.run(p, python="/old")
    ih.run(p, python="/new")
    got = json.loads(p.read_text())
    assert len(got["hooks"]["PermissionRequest"]) == 1
    assert got["hooks"]["PermissionRequest"][0]["hooks"][0]["command"].startswith("/new ")
    assert len(got["hooks"]["PostToolUse"]) == 1
    assert got["hooks"]["PostToolUse"][0]["hooks"][0]["command"].startswith("/new ")
    assert len(got["hooks"]["SessionStart"]) == 1
    assert got["hooks"]["SessionStart"][0]["hooks"][0]["command"].startswith("/new ")


def test_uninstall_removes_only_ours(tmp_path):
    p = _settings(tmp_path, {"hooks": {"PreToolUse": [OTHER]}})
    ih.run(p, python="/py")
    ih.run(p, uninstall=True)
    assert json.loads(p.read_text()) == {"hooks": {"PreToolUse": [OTHER]}}
    p2 = tmp_path / "second.json"
    p2.write_text("{}")
    ih.run(p2, python="/py"); ih.run(p2, uninstall=True)
    assert json.loads(p2.read_text()) == {}


def test_no_settings_file_yet_is_fine(tmp_path):
    p = tmp_path / "new" / "settings.json"
    ih.run(p, python="/py")
    assert "PermissionRequest" in json.loads(p.read_text())["hooks"]


def test_a_settings_file_that_is_not_an_object_is_left_alone(tmp_path):
    p = _settings(tmp_path, [1])
    with pytest.raises(SystemExit):
        ih.run(p)
    assert json.loads(p.read_text()) == [1]
