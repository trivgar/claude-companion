"""File settings select the ask and credential paths. The environment comes
first, as for every setting, so a test's temp path never loses to a real
config file and lands on the live store."""

from agent_companion.server import asks, auth


def test_asks_store_uses_its_config_path(monkeypatch, tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text('asks_dir = "~/asks"\n')
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_COMPANION_CONFIG", str(config_file))
    monkeypatch.delenv("AGENT_COMPANION_ASKS", raising=False)
    monkeypatch.delenv("AGENT_COMPANION_ASKS_DIR", raising=False)
    assert asks.store() == tmp_path / "asks"


def test_the_environment_beats_the_config_file(monkeypatch, tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text('''asks_dir = "~/asks"
owner_keys_file = "~/keys.json"
pairing_file = "~/pairs.json"
''')
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_COMPANION_CONFIG", str(config_file))
    monkeypatch.setenv("AGENT_COMPANION_ASKS", str(tmp_path / "env-asks"))
    monkeypatch.setenv("AGENT_COMPANION_OWNER_KEYS", str(tmp_path / "env-keys.json"))
    monkeypatch.setenv("AGENT_COMPANION_PAIRING", str(tmp_path / "env-pairs.json"))
    assert asks.store() == tmp_path / "env-asks"
    assert auth.owner_keys_path() == tmp_path / "env-keys.json"
    assert auth.pairing_path() == tmp_path / "env-pairs.json"


def test_credential_paths_use_their_config_paths(monkeypatch, tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text('''owner_keys_file = "~/keys.json"
pairing_file = "~/pairs.json"
hook_token_file = "~/token"
''')
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_COMPANION_CONFIG", str(config_file))
    for k in ("OWNER_KEYS", "PAIRING", "OWNER_KEYS_FILE", "PAIRING_FILE", "HOOK_TOKEN_FILE"):
        monkeypatch.delenv("AGENT_COMPANION_" + k, raising=False)
    assert auth.owner_keys_path() == tmp_path / "keys.json"
    assert auth.pairing_path() == tmp_path / "pairs.json"
    assert auth.token_path() == tmp_path / "token"
