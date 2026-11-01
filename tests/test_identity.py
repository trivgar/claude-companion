from claude_companion import server


def test_sprite_override_config_is_safe_and_keeps_only_hex_colours(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text('sprite_overrides = { "worker" = "#3b82f6", "<script>" = "#abcdef", bad = "blue" }\n')
    monkeypatch.setenv("CLAUDE_COMPANION_CONFIG", str(config))

    rendered = server._named(server.STATIC / "index.html")

    assert '"worker": "#3b82f6"' in rendered
    assert '"bad"' not in rendered
    assert "\\u003cscript\\u003e" in rendered
    assert '<script>' not in rendered.split('window.CC_IDENTITY_OVERRIDES=', 1)[1].split('</script>', 1)[0]
