"""Remote voice hosts are configured data, never built-in machines."""
from agent_companion import voices


def test_absent_host_and_cast_files_need_no_network(tmp_path):
    assert voices.hosts(tmp_path) == []
    assert voices.state(tmp_path, now=1, health=lambda url: (_ for _ in ()).throw(AssertionError(url)))["configured"] is False


def test_configured_hosts_report_fallback_without_names_in_code(tmp_path):
    (tmp_path / "remote-hosts.conf").write_text("primary=http://example.invalid\nbackup=http://example.invalid voice\n")
    (tmp_path / "remote-voices.conf").write_text("Speaker=voice\n")
    state = voices.state(tmp_path, now=100, health=lambda url: None)
    assert state["real"] is False and "primary" in state["why"]
