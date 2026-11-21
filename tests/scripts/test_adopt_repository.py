"""The adapter stamps the GitHub repository into the SOURCE_URL default of every Dockerfile."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STAMP = ROOT / "infra" / "adapt" / "adopt_stamp.py"
DOCKERFILES = ("Dockerfile.backend", "Dockerfile.frontend", "Dockerfile.nginx")
PLACEHOLDER = "ARG SOURCE_URL=https://github.com/your-org/your-repo\n"


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for name in DOCKERFILES:
        shutil.copy(ROOT / name, root / name)
    return root


def _stamp(root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-I",
            str(STAMP),
            "--root",
            str(root),
            "--domain",
            "example.test",
            "--registry",
            "registry.example.test",
            "--backup-dir",
            str(root.parent / "backup"),
            *extra,
        ],
        capture_output=True,
        text=True,
        check=True,
    )


def test_every_dockerfile_ships_the_one_placeholder(tree: Path) -> None:
    for name in DOCKERFILES:
        assert PLACEHOLDER in (tree / name).read_text(encoding="utf-8"), name


def test_the_repository_is_stamped_into_all_three_dockerfiles(tree: Path) -> None:
    out = _stamp(tree, "--repository", "acme/shop")
    for name in DOCKERFILES:
        text = (tree / name).read_text(encoding="utf-8")
        assert "ARG SOURCE_URL=https://github.com/acme/shop\n" in text, name
        assert "your-org" not in text, name
        assert name in out.stdout


def test_a_dry_run_lists_the_rewrites_and_changes_nothing(tree: Path) -> None:
    out = _stamp(tree, "--repository", "acme/shop", "--dry-run")
    for name in DOCKERFILES:
        assert name in out.stdout
        assert PLACEHOLDER in (tree / name).read_text(encoding="utf-8")


def test_without_a_repository_the_placeholder_stays(tree: Path) -> None:
    _stamp(tree)
    for name in DOCKERFILES:
        assert PLACEHOLDER in (tree / name).read_text(encoding="utf-8"), name


@pytest.mark.parametrize("bad", ["shop", "a/b/c", "acme/", "/shop", "ac me/shop"])
def test_a_repository_that_is_not_owner_slash_name_is_refused(tree: Path, bad: str) -> None:
    with pytest.raises(subprocess.CalledProcessError) as err:
        _stamp(tree, "--repository", bad)
    assert "owner/name" in err.value.stderr
