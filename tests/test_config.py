from claude_companion import config


def test_env_beats_the_file_and_the_file_beats_the_default(tmp_path, monkeypatch):
    f = tmp_path / "c.toml"
    f.write_text('url = "https://from-file"\narchive_roots = ["/a", "/b"]\n')
    monkeypatch.setenv("CLAUDE_COMPANION_CONFIG", str(f))
    assert config.get("url") == "https://from-file"
    assert config.paths("archive_roots") == (config.Path("/a"), config.Path("/b"))
    assert config.get("contact", "d") == "d"
    monkeypatch.setenv("CLAUDE_COMPANION_URL", "https://from-env")
    assert config.get("url") == "https://from-env"


def test_a_broken_file_is_the_default(tmp_path, monkeypatch):
    f = tmp_path / "c.toml"
    f.write_text("not = = toml")
    monkeypatch.setenv("CLAUDE_COMPANION_CONFIG", str(f))
    assert config.get("url", "d") == "d"
