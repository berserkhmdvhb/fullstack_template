"""OpenVEX + digest-pin template: `infra/security/sample-vex.json`.

Network-independent. Asserts the committed template is a representative OpenVEX
v0.2.0 shape AND that its product is pinned by immutable `@sha256:` digest (not a
mutable `:latest` / `:main` tag) and every `affected` statement carries an
`action_statement` (OpenVEX requires one) — so the shipped VEX reflects the real
build digest resolved at run time by `infra/security/generate-vex.sh`, and the "pin the immutable
digest" framing stays honest.
"""

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
tmpl = json.loads((REPO / "infra/security/sample-vex.json").read_text())


def test_valid_openvex_v020() -> None:
    # The real OpenVEX namespace is openvex.dev, not openvex.org.
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


def test_affected_requires_action_statement() -> None:
    for s in tmpl["statements"]:
        assert s["status"] != "not_affected"  # only the reviewed list may say that
        if s["status"] == "affected":
            assert s["action_statement"]
