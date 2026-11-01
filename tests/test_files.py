import inspect
import subprocess

from fastapi.routing import APIRoute

from agent_companion.server import auth, files  # noqa: E402
from agent_companion.server.features import FEATURES  # noqa: E402


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


def project(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.com")
    (root / ".gitignore").write_text("ignored.txt\n")
    (root / "note.txt").write_text("first\n")
    (root / "other.txt").write_text("other\n")
    (root / "ignored.txt").write_text("hidden\n")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "package.txt").write_text("generated\n")
    (root / "venv").mkdir()
    (root / "venv" / "data.txt").write_text("generated\n")
    (root / "binary.dat").write_bytes(b"a\0b")
    (root / "large.txt").write_bytes(b"x" * (files.MAX_BYTES + 1))
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n")
    (root / "linked.txt").symlink_to(outside)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "initial")
    config_file = tmp_path / "config.toml"
    config_file.write_text(f'[projects]\nworkspace = "{root}"\n')
    monkeypatch.setenv("AGENT_COMPANION_CONFIG", str(config_file))
    monkeypatch.delenv("AGENT_COMPANION_PROJECTS", raising=False)
    return root, outside


def _open(path):
    response = files.open_file("workspace", path)
    assert response["ok"], response
    return response


def _save(opened, text):
    return files.save("workspace", opened["path"], text, opened["mtime_ns"], opened["sha256"])


def test_tree_only_shows_small_text_files_that_git_does_not_ignore(tmp_path, monkeypatch):
    project(tmp_path, monkeypatch)
    data = files.tree("workspace")
    assert data["ok"]
    assert set(data["files"]) == {".gitignore", "note.txt", "other.txt"}
    assert not data["dirs"]


def test_routes_use_the_owner_read_and_write_guards():
    routes = {route.path: route for route in files.router.routes if isinstance(route, APIRoute)}
    for path in ("/api/files", "/api/files/tree", "/api/files/file"):
        assert auth.require_token in {item.call for item in routes[path].dependant.dependencies}
    assert auth.require_write in {item.call for item in routes["/api/files/save"].dependant.dependencies}


def test_path_escapes_and_symlink_escapes_are_refused(tmp_path, monkeypatch):
    project(tmp_path, monkeypatch)
    for path in ("../outside.txt", "/outside.txt", "linked.txt"):
        assert not files.open_file("workspace", path)["ok"]
    assert not files.tree("workspace", "../")["ok"]


def test_dirty_file_save_does_not_commit(tmp_path, monkeypatch):
    root, _ = project(tmp_path, monkeypatch)
    opened = _open("note.txt")
    (root / "note.txt").write_text("work in progress\n")
    opened = _open("note.txt")
    head = _git(root, "rev-parse", "HEAD").stdout.strip()
    response = _save(opened, "saved beside existing work\n")
    assert response["ok"] and response["committed"] is False
    assert "uncommitted changes" in response["detail"]
    assert _git(root, "rev-parse", "HEAD").stdout.strip() == head
    assert (root / "note.txt").read_text() == "saved beside existing work\n"


def test_clean_file_save_commits_only_that_file_and_leaves_other_staging(tmp_path, monkeypatch):
    root, _ = project(tmp_path, monkeypatch)
    (root / "other.txt").write_text("staged elsewhere\n")
    _git(root, "add", "other.txt")
    opened = _open("note.txt")
    response = _save(opened, "saved cleanly\n")
    assert response["ok"] and response["committed"] is True
    assert _git(root, "log", "-1", "--format=%s").stdout.strip() == "Edit note.txt from the dashboard"
    assert _git(root, "show", "--format=", "--name-only", "HEAD").stdout.splitlines() == ["note.txt"]
    assert _git(root, "diff", "--cached", "--name-only").stdout.splitlines() == ["other.txt"]


def test_stale_edit_is_refused_before_a_write(tmp_path, monkeypatch):
    root, _ = project(tmp_path, monkeypatch)
    opened = _open("note.txt")
    (root / "note.txt").write_text("changed elsewhere\n")
    response = _save(opened, "dashboard edit\n")
    assert response["status"] == 409
    assert "changed on disk" in response["detail"]
    assert (root / "note.txt").read_text() == "changed elsewhere\n"


def test_feature_is_listed_and_never_runs_a_push_command():
    assert "files" in FEATURES
    assert "push" not in inspect.getsource(files).lower()
