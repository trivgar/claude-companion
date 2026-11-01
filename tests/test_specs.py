import os
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.routing import APIRoute  # noqa: E402

from claude_companion.server import auth, specs  # noqa: E402


def _config(tmp_path, monkeypatch, projects, extra=""):
    path = tmp_path / "config.toml"
    rows = "\n".join(f'{name} = "{root}"' for name, root in projects.items())
    path.write_text(f"{extra}[projects]\n{rows}\n")
    monkeypatch.setenv("CLAUDE_COMPANION_CONFIG", str(path))
    monkeypatch.delenv("CLAUDE_COMPANION_SPEC_GLOBS", raising=False)


def _write(root, relative, text, modified):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(path, (modified, modified))
    return path


def test_listing_is_newest_first_and_uses_headings_titles_and_extra_globs(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _write(first, "docs/superpowers/specs/2026-02-01.md", "# First spec\n", 1_000)
    _write(first, "docs/superpowers/plans/2026-03-01-work.md", "intro\n## Work plan\n", 2_000)
    _write(first, "docs/design/2026-04-01.md", "no heading\n", 3_000)
    _write(second, "docs/superpowers/specs/2026-05-01-later.md", "# Later\n", 4_000)
    _config(tmp_path, monkeypatch, {"first": first, "second": second}, 'spec_globs = ["docs/design/*.md"]\n')

    docs = specs.listing()

    assert [(doc["project"], doc["title"], doc["kind"], doc["date"]) for doc in docs] == [
        ("second", "Later", "spec", "2026-05-01"),
        ("first", "2026-04-01.md", "spec", "2026-04-01"),
        ("first", "Work plan", "plan", "2026-03-01"),
        ("first", "First spec", "spec", "2026-02-01"),
    ]


def test_undated_documents_use_mtime_and_extra_plan_paths_are_plans(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    _write(root, "notes/plans/no-date.md", "# An extra plan\n", 1_700_000_000)
    _config(tmp_path, monkeypatch, {"project": root}, 'spec_globs = ["notes/**/*.md"]\n')

    doc = specs.listing()[0]

    assert doc["kind"] == "plan"
    assert doc["date"] == "2023-11-14"


def test_path_escapes_symlinks_and_large_files_are_refused(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n")
    linked = root / "docs" / "superpowers" / "specs" / "linked.md"
    linked.parent.mkdir(parents=True)
    linked.symlink_to(outside)
    large = _write(root, "docs/superpowers/specs/large.md", "x" * (specs.MAX_BYTES + 1), 1_000)
    _write(root, "docs/superpowers/specs/inside.md", "# Inside\n", 2_000)
    _config(tmp_path, monkeypatch, {"project": root}, 'spec_globs = ["../*.md"]\n')

    assert [doc["path"] for doc in specs.listing()] == ["docs/superpowers/specs/inside.md"]
    assert specs.read("project", "../outside.md")["ok"] is False
    assert specs.read("project", str(outside))["ok"] is False
    assert specs.read("project", "docs/superpowers/specs/linked.md")["ok"] is False
    assert specs.read("project", "docs/superpowers/specs/large.md")["ok"] is False
    assert large.stat().st_size > specs.MAX_BYTES


def test_routes_use_the_owner_read_guard():
    routes = {route.path: route for route in specs.router.routes if isinstance(route, APIRoute)}
    for path in ("/api/specs", "/api/specs/file"):
        assert auth.require_token in {item.call for item in routes[path].dependant.dependencies}
