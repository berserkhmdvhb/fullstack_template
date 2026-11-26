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


def test_a_relative_pnpm_link_survives_with_the_cwd_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path / "bundle")
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(tmp_path / "elsewhere")
    work = tmp_path / "work"
    work.mkdir()
    copy_standalone(bundle, work)
    link = work / "node_modules" / "next"
    assert link.is_symlink()
    assert (link / "package.json").read_text(encoding="utf-8") == '{"name": "next"}'


def test_a_link_out_of_the_copy_fails_naming_it(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "bundle")
    (tmp_path / "outside").mkdir()
    (bundle / "escape").symlink_to(tmp_path / "outside")
    with pytest.raises(AssertionError, match="escape"):
        copy_standalone(bundle, tmp_path / "work")
