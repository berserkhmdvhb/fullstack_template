"""Honest roadmap + commit-pinned VEX tooling.

Network-independent. Asserts two things the "captured, never faked" / honesty
rule requires:

1. `wiki/ROADMAP-supply-chain.md` §4 no longer lists the legacy syft/grype/grant
   `curl | bash` install debt as *outstanding* — all three now install through
   the checksum-verified `infra/security/tools-install.sh`, so it is retired.
2. The VEX template's `tooling` points at the producing **commit**
   (`blob/${GITHUB_SHA}/…`), not a moving `blob/main`, and the generator
   builds each statement's `@id` from the match's OSV advisory reference — so a
   shipped VEX doc is traceable to the commit that produced it.
"""

import re
from pathlib import Path

from tests.supply_chain._workflows import workflow

REPO = Path(__file__).resolve().parent.parent.parent
ROADMAP = REPO / "wiki/ROADMAP-supply-chain.md"
SAMPLE_VEX = REPO / "infra/security/sample-vex.json"
GENERATE_VEX = REPO / "infra/security/generate-vex.sh"


def _section4() -> str:
    blob = ROADMAP.read_text()
    m = re.search(r"##\s*4\..*?(?=\n##\s|\Z)", blob, re.DOTALL)
    return m.group(0) if m else blob


def test_roadmap_section4_no_longer_lists_resolved_curl_bash() -> None:
    s4 = _section4()
    low = s4.lower()
    # The curl|bash debt must not stand as outstanding: either the "still install
    # their tools with unverified" claim is gone, or it is explicitly marked done.
    assert (
        "still install their tools with unverified" not in s4 or "done" in low or "retired" in low
    )


def test_roadmap_section4_is_marked_done_not_roadmap() -> None:
    # §4's header frames it as retired history, not a roadmap-only item.
    header = _section4().splitlines()[0]
    assert "retired" in header.lower() or "done" in header.lower()


def test_roadmap_summary_drops_curl_bash_node() -> None:
    # The mermaid summary must not still show the curl|bash→tools-install roadmap node.
    blob = ROADMAP.read_text()
    assert "curl|bash → tools-install" not in blob


def test_vex_tooling_is_commit_pinned_not_blob_main() -> None:
    blob = SAMPLE_VEX.read_text()
    assert "blob/main" not in blob, (
        "VEX tooling must reference the producing commit, not a moving main"
    )
    assert "${GITHUB_SHA}" in blob, "tooling is pinned to ${GITHUB_SHA}, filled at run time"


def test_vex_statement_id_is_osv_advisory_reference() -> None:
    # generate-vex.sh builds each statement @id from the match's OSV advisory
    # reference (osv.dev resolves any id: CVE/GHSA/OSV), not the project's internal path.
    src = GENERATE_VEX.read_text()
    assert "osv.dev/vulnerability/" in src
    assert "$proj" not in src  # the old project-path @id is gone


def test_vex_job_is_pinned_to_image_digests_not_tags() -> None:
    # The VEX is generated from the digests the release job recorded (image-digests.env),
    # on the same commit (GITHUB_SHA is the runner's), never from a moving tag.
    runs = "\n".join(s.get("run", "") for s in workflow("release.yml")["jobs"]["vex"]["steps"])
    assert "infra/security/generate-vex.sh" in runs
    assert "image-digests.env" in runs
