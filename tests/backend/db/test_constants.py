# your-service-name — adaptable service template (see wiki/getting-started.md)
"""project_root in an installed layout: no marker above the module, so fall back to the cwd."""

from __future__ import annotations

from pathlib import Path

import pytest

from your_service_name.backend.db._constants import default_sqlite_url, project_root


def test_project_root_finds_marker_above_start(tmp_path: Path) -> None:
    (tmp_path / "alembic.ini").write_text("")
    mod = tmp_path / "pkg" / "mod.py"
    mod.parent.mkdir()
    mod.write_text("")
    assert project_root(mod) == tmp_path


def test_installed_layout_falls_back_to_cwd_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """site-packages has no marker above it; the container WORKDIR does (/app/alembic.ini)."""
    site = tmp_path / "site-packages" / "pkg"
    site.mkdir(parents=True)
    mod = site / "run_migrations.py"
    mod.write_text("")
    app = tmp_path / "app"
    app.mkdir()
    (app / "alembic.ini").write_text("")
    monkeypatch.chdir(app)
    assert project_root(mod) == app
    assert default_sqlite_url(project_root(mod)).endswith(f"{app}/data/items.db")


def test_root_is_never_a_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = tmp_path / "mod.py"
    mod.write_text("")
    monkeypatch.chdir(tmp_path)
    assert project_root(mod).is_dir()
