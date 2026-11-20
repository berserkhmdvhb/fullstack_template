"""OpenVEX + digest-pin template: `infra/security/sample-vex.json`.

Network-independent. Asserts the committed template is a representative OpenVEX
v0.2.0 shape AND that its product is pinned by immutable `@sha256:` digest (not a
mutable `:latest` / `:main` tag) and every `not_affected` statement carries
`fixed_version` — so the shipped VEX reflects the real build digest resolved at
run time by `infra/security/generate-vex.sh`, and the "pin the immutable
digest" framing stays honest.
"""

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
tmpl = json.loads((REPO / "infra/security/sample-vex.json").read_text())


def test_valid_openvex_v020() -> None:
    # Real OpenVEX namespace is openvex.dev (the plan's "openvex.org" is a typo).
    assert tmpl["@context"] == "https://openvex.dev/ns/v0.2.0"
    assert "author" in tmpl
    assert "timestamp" in tmpl
    assert "statements" in tmpl
    assert isinstance(tmpl["statements"], list)
    assert tmpl["statements"]


def test_product_pinned_by_digest_not_tag() -> None:
    prod = tmpl["statements"][0]["products"][0]["@id"]
    assert "@sha256:" in prod  # pinned by immutable digest
    assert ":latest" not in prod  # not by mutable tag
    assert ":main" not in prod


def test_not_affected_requires_fixed_version() -> None:
    for s in tmpl["statements"]:
        if s["status"] == "not_affected":
            assert "fixed_version" in json.dumps(s)
            assert s["justification"] == "fixed_version"
