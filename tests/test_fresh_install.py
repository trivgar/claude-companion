"""Found running a fresh public clone the way a new user would (2026-11-01)."""
from claude_companion import config, limits
from claude_companion.server import comms


def test_a_broken_config_file_says_so(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    monkeypatch.setenv("CLAUDE_COMPANION_CONFIG", str(path))
    assert config.problem() == ""                      # absent is fine
    path.write_text('server_port = 1\nserver_port = 2\n')
    assert "not valid TOML" in config.problem() and "default" in config.problem()
    path.write_text('server_port = 1\n')
    assert config.problem() == ""


def test_the_web_player_hears_not_set_up_without_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_COMPANION_TTS_DIR", str(tmp_path / "none"))
    assert comms.tts_outbox(who="web") == {"set_up": False, "clips": []}


def test_a_rate_limited_usage_check_reads_as_words(monkeypatch):
    import urllib.error, urllib.request
    def refuse(*a, **k):
        raise urllib.error.HTTPError("u", 429, "Too Many", {}, None)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    assert limits.fetch("t") == (None, "rate-limited by Anthropic, will retry")
