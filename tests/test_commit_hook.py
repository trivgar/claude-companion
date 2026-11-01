import io
import json

from agent_companion.hooks import commit_hook as hook


class Result:
    returncode = 0
    stdout = "a" * 40 + "\x00Record the change\n"


def event(command="git commit -m done"):
    return {"tool_name": "Bash", "tool_input": {"command": command},
            "session_id": "session-1", "cwd": "/work/project"}


def test_successful_commit_from_fake_hook_input_is_appended(tmp_path, monkeypatch):
    output = tmp_path / "commits.jsonl"
    monkeypatch.setattr(hook, "log_path", lambda: output)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return Result()

    assert hook.main(io.StringIO(json.dumps(event())), now=123.5, run=run) == 0
    assert calls == [["git", "-C", "/work/project", "log", "-1", "--format=%H%x00%s"]]
    assert json.loads(output.read_text()) == {"at": 123.5, "session_id": "session-1",
                                              "cwd": "/work/project", "sha": "a" * 40,
                                              "subject": "Record the change"}
    assert oct(output.stat().st_mode & 0o777) == "0o600"


def test_only_real_non_dry_run_git_commits_are_recorded(tmp_path, monkeypatch):
    output = tmp_path / "commits.jsonl"
    monkeypatch.setattr(hook, "log_path", lambda: output)
    for command in ("pytest -q", "echo git commit", "git commit --dry-run"):
        assert hook.main(io.StringIO(json.dumps(event(command))), run=lambda *a, **k: Result()) == 0
    assert not output.exists()


def test_git_c_directory_is_recorded_and_used_for_the_lookup(tmp_path, monkeypatch):
    output = tmp_path / "commits.jsonl"
    monkeypatch.setattr(hook, "log_path", lambda: output)
    seen = []

    def run(args, **kwargs):
        seen.append(args)
        return Result()

    assert hook.main(io.StringIO(json.dumps(event("git -C nested commit -m done"))), now=1, run=run) == 0
    assert seen[0][2] == "/work/project/nested"
    assert json.loads(output.read_text())["cwd"] == "/work/project/nested"


def test_configured_commit_log_file_is_used(tmp_path, monkeypatch):
    configured = tmp_path / "records" / "made.jsonl"
    monkeypatch.setattr(hook.config, "get", lambda key: str(configured) if key == "commit_log_file" else None)
    assert hook.log_path() == configured
