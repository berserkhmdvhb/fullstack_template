"""The standalone copy used by the live suite, tested on a tmp tree shaped like pnpm's."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.utils.standalone_copy import copy_standalone

_STORE = Path("node_modules/.pnpm/next@0.0.0/node_modules/next")


def _bundle(root: Path) -> Path:
    (root / _STORE).mkdir(parents=True)
    (root / _STORE / "package.json").write_text('{"name": "next"}', encoding="utf-8")
    (root / "node_modules" / "next").symlink_to(Path(".pnpm/next@0.0.0/node_modules/next"))
    return root


def _static(root: Path) -> Path:
    (root / "static").mkdir(exist_ok=True)
    return root / "static"


def test_a_relative_pnpm_link_survives_with_the_cwd_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path / "bundle")
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(tmp_path / "elsewhere")
    work = tmp_path / "work"
    work.mkdir()
    copy_standalone(bundle, work, static=_static(tmp_path), public=tmp_path / "missing")
    link = work / "node_modules" / "next"
    assert link.is_symlink()
    assert (link / "package.json").read_text(encoding="utf-8") == '{"name": "next"}'


def test_a_link_out_of_the_copy_fails_naming_it(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "bundle")
    (tmp_path / "outside").mkdir()
    (bundle / "escape").symlink_to(tmp_path / "outside")
    with pytest.raises(AssertionError, match="escape"):
        copy_standalone(
            bundle, tmp_path / "work", static=_static(tmp_path), public=tmp_path / "missing"
        )


def _build_host_static(tmp_path: Path) -> tuple[Path, Path]:
    """A bundle whose ``.next/static`` links to the build host, and the build's own static dir."""
    bundle = _bundle(tmp_path / "bundle")
    static = tmp_path / "build" / ".next" / "static"
    (static / "chunks").mkdir(parents=True)
    (static / "chunks" / "app.js").write_text("app", encoding="utf-8")
    (bundle / ".next").mkdir()
    (bundle / ".next" / "static").symlink_to(static)
    return bundle, static


def test_a_build_host_static_link_is_replaced_before_the_check(tmp_path: Path) -> None:
    bundle, static = _build_host_static(tmp_path)
    work = tmp_path / "work"
    copy_standalone(bundle, work, static=static, public=tmp_path / "build" / "public")
    assert not (work / ".next" / "static").is_symlink()
    assert (work / ".next" / "static" / "chunks" / "app.js").read_text(encoding="utf-8") == "app"


def test_a_link_out_of_the_copy_elsewhere_still_fails_naming_it(tmp_path: Path) -> None:
    bundle, static = _build_host_static(tmp_path)
    (bundle / "node_modules" / "leak").symlink_to(tmp_path / "build")
    with pytest.raises(AssertionError, match="leak"):
        copy_standalone(bundle, tmp_path / "work", static=static, public=tmp_path / "missing")
