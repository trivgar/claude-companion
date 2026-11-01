import json
import os
from datetime import datetime
from pathlib import Path

from agent_companion.codex_limits import CodexLimits


NOW = 1_800_000_000.0


def _event(when, primary, secondary=None):
    return {"timestamp": datetime.fromtimestamp(when).astimezone().isoformat(),
            "type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
                "primary": primary, "secondary": secondary}}}


def _rollout(home: Path, *events, modified=NOW) -> Path:
    folder = home / "sessions" / datetime.fromtimestamp(NOW).strftime("%Y/%m/%d")
    folder.mkdir(parents=True)
    path = folder / "rollout-example.jsonl"
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n")
    os.utime(path, (modified, modified))
    return path


def _window(used, minutes, reset):
    return {"used_percent": used, "window_minutes": minutes, "resets_at": reset}


def test_missing_sessions_folder_keeps_the_feature_off(tmp_path):
    assert CodexLimits(tmp_path / "missing").read(NOW) == {"enabled": False}


def test_empty_sessions_folder_explains_the_missing_reading(tmp_path):
    (tmp_path / "home" / "sessions").mkdir(parents=True)
    value = CodexLimits(tmp_path / "home").read(NOW)
    assert value["enabled"] is True and value["available"] is False
    assert "no Codex session logs" in value["reason"]


def test_reads_the_last_token_count_for_week_and_five_hour(tmp_path):
    home = tmp_path / "home"
    _rollout(home,
             _event(NOW - 200, _window(11, 10080, NOW + 30_000), _window(22, 300, NOW + 10_000)),
             _event(NOW - 120, _window(18, 10080, NOW + 30_000), _window(37, 300, NOW + 10_000)))
    value = CodexLimits(home).read(NOW)
    assert value["available"] is True and value["age_seconds"] == 120
    assert value["week"]["utilization"] == 18
    assert value["five_hour"]["utilization"] == 37


def test_reads_relative_reset_seconds_and_a_week_only_plan(tmp_path):
    home = tmp_path / "home"
    _rollout(home, _event(NOW - 30,
                          {"used_percent": 12, "window_minutes": 10080, "resets_in_seconds": 500}))
    value = CodexLimits(home).read(NOW)
    assert value["available"] is True and value["week"]["utilization"] == 12
    assert value["five_hour"] is None
    assert 469 <= value["week"]["resets_in_seconds"] <= 471


def test_expired_reading_reports_words_instead_of_an_invented_zero(tmp_path):
    home = tmp_path / "home"
    _rollout(home, _event(NOW - 400,
                          _window(18, 10080, NOW - 1), _window(37, 300, NOW - 1)))
    value = CodexLimits(home).read(NOW)
    assert value["enabled"] is True and value["available"] is False
    assert "reset" in value["reason"]
    assert "five_hour" not in value and "week" not in value


def test_unchanged_rollout_is_not_reparsed_but_a_changed_one_is(tmp_path, monkeypatch):
    home = tmp_path / "home"
    path = _rollout(home, _event(NOW - 10, _window(18, 10080, NOW + 30_000)))
    reader = CodexLimits(home)
    calls = []
    original = reader._tail_reading
    monkeypatch.setattr(reader, "_tail_reading", lambda file: calls.append(file) or original(file))
    assert reader.read(NOW)["week"]["utilization"] == 18
    assert reader.read(NOW + 10)["week"]["utilization"] == 18
    assert calls == [path]
    with path.open("a") as output:
        output.write(json.dumps(_event(NOW, _window(29, 10080, NOW + 30_000))) + "\n")
    assert reader.read(NOW + 11)["week"]["utilization"] == 29
    assert calls == [path, path]


def test_sessions_usage_and_strip_include_the_optional_codex_reading():
    root = Path(__file__).resolve().parents[1]
    route = (root / "agent_companion/server/sessions.py").read_text()
    strip = (root / "agent_companion/server/static/sessions.js").read_text()
    assert 'out["codex"] = codex_limits.read()' in route
    assert "Codex ${codex.join(' · ')}" in strip
    assert "5h ${Math.round(c.five_hour.utilization)}%" in strip
    assert "as of ${ago(c.age_seconds)}" in strip
