from agent_companion import newsession, projects
from agent_companion.server import files


def _config(tmp_path, monkeypatch, text):
    path = tmp_path / "config.toml"
    path.write_text(text)
    monkeypatch.setenv("AGENT_COMPANION_CONFIG", str(path))
    monkeypatch.delenv("AGENT_COMPANION_PROJECTS", raising=False)
    monkeypatch.delenv("AGENT_COMPANION_NEW_SESSION", raising=False)


def test_one_projects_table_feeds_files_and_the_launcher(tmp_path, monkeypatch):
    api = tmp_path / "api"
    api.mkdir()
    _config(tmp_path, monkeypatch, f'[projects]\napi = "{api}"\n')
    assert [p["name"] for p in files.projects()] == ["api"]
    assert [p.name for p in newsession.settings().projects] == ["api"]


def test_the_old_new_session_list_still_works_and_projects_wins_a_shared_name(tmp_path, monkeypatch):
    one, two, old = tmp_path / "one", tmp_path / "two", tmp_path / "old"
    for d in (one, two, old):
        d.mkdir()
    _config(tmp_path, monkeypatch,
            f'[projects]\none = "{one}"\n\n'
            f'[new_session]\ndefault_model = "sonnet"\n'
            f'projects = [{{ name = "one", path = "{old}" }}, {{ name = "two", path = "{two}" }}]\n')
    assert projects.configured() == [("one", str(one)), ("two", str(two))]
    got = newsession.settings()
    assert [(p.name, p.path) for p in got.projects] == [("one", one.resolve()), ("two", two.resolve())]
    assert got.default_model == "sonnet"
    assert [p["name"] for p in files.projects()] == ["one", "two"]


def test_pairs_skips_malformed_rows():
    assert projects.pairs([{"name": "a", "path": "/x"}, {"name": ""}, "junk", {"name": "b", "path": 3}]) == [("a", "/x")]
    assert projects.pairs("a string") == []
