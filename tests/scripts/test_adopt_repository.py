"""The adapter stamps the GitHub repository into the SOURCE_URL default of every Dockerfile."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STAMP = ROOT / "infra" / "adapt" / "adopt_stamp.py"
DOCKERFILES = ("Dockerfile.backend", "Dockerfile.frontend", "Dockerfile.waf")
BASH = shutil.which("bash") or "/usr/bin/bash"
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


def test_the_repository_is_stamped_into_every_dockerfile(tree: Path) -> None:
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


def test_the_placeholder_source_url_is_defined_once_and_matches_the_dockerfiles() -> None:
    """image-checks.sh reads OLD_SOURCE_URL from the adapter; the Dockerfiles carry the same."""
    out = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                "import sys; sys.path.insert(0, sys.argv[1]); import adopt_stamp as a; "
                "print(a.OLD_SOURCE_URL); print(a.OLD_REPOSITORY)"
            ),
            str(STAMP.parent),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert out[0] == f"https://github.com/{out[1]}"
    for name in DOCKERFILES:
        assert f"ARG SOURCE_URL={out[0]}\n" in (ROOT / name).read_text(encoding="utf-8")


# The governance files and the README carry the repository too; CODEOWNERS takes CODEOWNER.
GOVERNANCE = (
    "README.md",
    "SECURITY.md",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/pull_request_template.md",
)


@pytest.fixture
def governance(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for name in (*GOVERNANCE, ".github/CODEOWNERS", "CODE_OF_CONDUCT.md"):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, root / name)
    return root


def test_the_badges_and_governance_links_carry_the_placeholder_repository() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for badge in ("ci.yml/badge.svg", "api.scorecard.dev", "license-Apache", "gh-badge-level3"):
        assert badge in readme, badge
    assert "github.com/<owner>" not in readme
    for name in GOVERNANCE:
        assert "your-org/your-repo" in (ROOT / name).read_text(encoding="utf-8"), name
    assert "@your-org\n" in (ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")


def test_the_repository_the_code_owner_and_the_domain_are_stamped(governance: Path) -> None:
    out = _stamp(governance, "--repository", "acme/shop", "--codeowner", "@acme/core")
    for name in GOVERNANCE:
        text = (governance / name).read_text(encoding="utf-8")
        assert "github.com/acme/shop" in text, name
        assert "your-org" not in text, name
        assert name in out.stdout
    owners = (governance / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    assert "*                    @acme/core\n" in owners
    assert "@your-org" not in owners
    assert "CODEOWNER is unset" not in out.stdout
    conduct = (governance / "CODE_OF_CONDUCT.md").read_text(encoding="utf-8")
    assert "conduct@example.test" in conduct


def test_without_a_repository_the_governance_placeholders_stay(governance: Path) -> None:
    _stamp(governance)
    for name in (*GOVERNANCE, ".github/CODEOWNERS"):
        assert "your-org" in (governance / name).read_text(encoding="utf-8"), name


def test_the_repository_alone_keeps_the_code_owner_placeholder_with_a_notice(
    governance: Path,
) -> None:
    out = _stamp(governance, "--repository", "acme/shop")
    owners = (governance / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    assert "*                    @your-org\n" in owners
    assert "@acme" not in owners  # never the bare organisation: GitHub rejects it
    assert "[adopt] NOTE: CODEOWNER is unset" in out.stdout


@pytest.mark.parametrize("owner", ["@octocat", "@acme/core-team", "dev@example.test"])
def test_a_user_a_team_or_an_email_is_a_code_owner(governance: Path, owner: str) -> None:
    _stamp(governance, "--codeowner", owner)
    owners = (governance / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    assert f"*                    {owner}\n" in owners


@pytest.mark.parametrize("bad", ["octocat", "@", "@a/b/c", "@ac me", "a@b", "@a\\1", "a\\1@b.c"])
def test_a_code_owner_of_another_shape_is_refused(governance: Path, bad: str) -> None:
    with pytest.raises(subprocess.CalledProcessError) as err:
        _stamp(governance, "--codeowner", bad)
    assert "@org/team" in err.value.stderr
    assert "@your-org" in (governance / ".github" / "CODEOWNERS").read_text(encoding="utf-8")


def test_adopt_sh_passes_codeowner_from_the_config(governance: Path) -> None:
    shutil.copy(ROOT / "pyproject.toml", governance / "pyproject.toml")
    conf = governance.parent / "adopt.conf"
    conf.write_text(
        'SERVICE_NAME="your_service_name"\nDOMAIN="example.test"\n'
        'REGISTRY="registry.example.test"\nCODEOWNER="@acme/core"\n',
        encoding="utf-8",
    )
    subprocess.run(
        [BASH, str(STAMP.parent / "adopt.sh")],
        env={**os.environ, "ADOPT_CONF": str(conf), "ADAPT_ROOT": str(governance)},
        capture_output=True,
        text=True,
        check=True,
    )
    owners = (governance / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    assert "*                    @acme/core\n" in owners
