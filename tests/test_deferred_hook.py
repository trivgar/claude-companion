import json

from agent_companion import deferred
from agent_companion.hooks import deferred_hook


def test_hook_outputs_open_and_answered_handoffs_from_fixture_file(tmp_path, monkeypatch):
    path, state = tmp_path / "deferred.md", tmp_path / "seen"
    source, receiver = tmp_path / "source", tmp_path / "receiver"
    source.mkdir()
    receiver.mkdir()
    monkeypatch.setenv("AGENT_COMPANION_DEFERRED_FILE", str(path))
    monkeypatch.setenv("AGENT_COMPANION_DEFERRED_HOOK_STATE_DIR", str(state))

    open_entry = deferred.add("review the change", by="sender", for_project="receiver", path=path, cwd=source)
    answered_entry = deferred.add("supply the result", by="sender", for_project="receiver", path=path, cwd=source)
    deferred.answer(answered_entry["header"], "the result is ready", path=path)

    open_output = deferred_hook.run({"cwd": str(receiver), "session_id": "receiver-session",
                                     "hook_event_name": "SessionStart"})
    context = json.loads(open_output)["hookSpecificOutput"]
    assert context["hookEventName"] == "SessionStart"
    assert "review the change" in context["additionalContext"]
    assert "supply the result" not in context["additionalContext"]
    assert deferred_hook.run({"cwd": str(receiver), "session_id": "receiver-session",
                              "hook_event_name": "UserPromptSubmit"}) == ""

    asked_output = deferred_hook.run({"cwd": str(source), "session_id": "source-session",
                                      "hook_event_name": "UserPromptSubmit"})
    asked_context = json.loads(asked_output)["hookSpecificOutput"]["additionalContext"]
    assert "Answered requests from source" in asked_context
    assert "the result is ready" in asked_context
    assert open_entry["header"] not in asked_context


def test_a_cleared_session_is_told_again_though_the_file_did_not_change(tmp_path, monkeypatch):
    path, state = tmp_path / "deferred.md", tmp_path / "seen"
    source, receiver = tmp_path / "source", tmp_path / "receiver"
    source.mkdir()
    receiver.mkdir()
    monkeypatch.setenv("AGENT_COMPANION_DEFERRED_FILE", str(path))
    monkeypatch.setenv("AGENT_COMPANION_DEFERRED_HOOK_STATE_DIR", str(state))
    deferred.add("review the change", by="sender", for_project="receiver", path=path, cwd=source)
    start = {"cwd": str(receiver), "session_id": "s", "hook_event_name": "SessionStart"}
    assert "review the change" in deferred_hook.run(start)
    assert deferred_hook.run(dict(start, hook_event_name="UserPromptSubmit")) == ""
    assert "review the change" in deferred_hook.run(start)
