"""prek runs the git hooks: a locked, attested aqua tool in .tools/bin, and pre-commit is gone."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from tests.supply_chain._pins import listing

ROOT = Path(__file__).resolve().parent.parent.parent
KEY = "aqua:j178/prek"
CONFIG = tomllib.loads((ROOT / "mise.toml").read_text())
LOCK = tomllib.loads((ROOT / "mise.lock").read_text())


def test_prek_resolves_from_mise_toml_and_links_into_tools_bin() -> None:
    assert KEY in CONFIG["tools"]
    assert listing()["prek"][1] == KEY
    assert re.fullmatch(r"\d+\.\d+\.\d+", CONFIG["tools"][KEY])


@pytest.mark.parametrize("platform", ["linux-x64", "macos-arm64"])
def test_prek_is_locked_with_a_checksum_and_an_attestation(platform: str) -> None:
    entry = LOCK["tools"][KEY][0]
    assert entry["version"] == CONFIG["tools"][KEY]
    locked = entry[f"platforms.{platform}"]
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", locked["checksum"])
    assert locked["provenance"] == "github-attestations"


def test_no_pre_commit_row_or_requirements_file_remains() -> None:
    pins = (ROOT / "tools/pins.tsv").read_text().splitlines()
    assert not [ln for ln in pins if ln.split("\t")[0] == "pre-commit"]
    assert not (ROOT / "tools/requirements/pre-commit.txt").exists()
    assert "pre-commit" not in (ROOT / "scripts/pins_refresh.py").read_text()


def test_make_precommit_runs_prek() -> None:
    text = (ROOT / "Makefile").read_text()
    body = text.split("\nprecommit:\n", 1)[1].split("\n\n", 1)[0]
    assert ".tools/bin/prek run --all-files" in body
    assert "pre-commit" not in body
