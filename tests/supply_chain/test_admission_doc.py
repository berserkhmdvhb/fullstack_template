"""The versions `wiki/admission-control.md` states must equal mise.toml."""

from __future__ import annotations

import re

from tests.supply_chain._pins import pin
from tests.supply_chain._workflows import REPO

DOC = (REPO / "wiki" / "admission-control.md").read_text(encoding="utf-8")


def table_version(label: str) -> str:
    m = re.search(rf"^\| {re.escape(label)}[^|]*\| ([0-9][^ |]*) \|", DOC, re.MULTILINE)
    assert m, f"the version table has no row for {label}"
    return m.group(1)


def test_cosign_version_matches_pins() -> None:
    assert table_version("cosign") == pin("cosign")


def test_kyverno_cli_version_matches_pins() -> None:
    assert table_version("Kyverno CLI") == pin("kyverno")


def test_in_cluster_minor_matches_cli_minor() -> None:
    m = re.search(r"^\| Kyverno in the cluster \| (\d+\.\d+)\.x", DOC, re.MULTILINE)
    assert m, "the version table has no in-cluster Kyverno row"
    assert pin("kyverno").startswith(m.group(1) + ".")


def test_links_to_the_single_identity_section() -> None:
    assert "./security.md#verifying-an-image" in DOC
    assert "reusable-build.yml@" not in DOC


def test_tells_adopters_to_render_and_to_check_with_a_server_side_dry_run() -> None:
    assert "scripts/render-admission-policy.sh <Owner>/<Repo>" in DOC
    assert (REPO / "scripts" / "render-admission-policy.sh").is_file()
    assert "--dry-run=server" in DOC


def test_gke_section_cites_the_current_google_pages() -> None:
    for page in ("overview-cv", "cv-sigstore-check", "cv-slsa-check", "policy-yaml-reference"):
        assert f"https://docs.cloud.google.com/binary-authorization/docs/{page}" in DOC
