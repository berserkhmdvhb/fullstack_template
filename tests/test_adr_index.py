# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Every ADR in wiki/adr has the required sections and a valid status, and the index lists each.

The seven ADRs of the minimum set (GitHub only, IaC placement, one Uvicorn process, migrations
as a Job, the edge modes, uv, the supply-chain level) must exist, and no ADR may restate a version
pinned in tools/pins.tsv or tools/kind-nodes.tsv: Renovate moves those files, not the ADRs."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADR_DIR = ROOT / "wiki" / "adr"
ADRS = sorted(
    p for p in ADR_DIR.glob("[0-9][0-9][0-9][0-9]-*.md") if not p.name.startswith("0000-")
)
MINIMUM_SET = {
    "0001": "github-only",
    "0003": "platform-iac-outside-the-template",
    "0004": "one-uvicorn-process-per-pod",
    "0006": "migrations-as-a-release-job",
    "0008": "edge-modes",
    "0015": "uv-for-python",
    "0016": "slsa-build-l3-for-release-images",
}
SECTIONS = (
    "Status", "Date", "Context", "Decision", "Consequences", "Alternatives considered", "Links",
)  # fmt: skip
STATUS = re.compile(r"(?:(?:Accepted|Deferred)\b|Superseded by \[ADR-\d{4}\]\([^)]+\))")
INDEX_ROW = re.compile(r"^\| \[(\d{4})\]\(\./([^)]+)\) \| [^|]+ \| (\w+) \|", re.MULTILINE)
# Versions that are themselves the decision, so an ADR may state them: the Kubernetes floor
# (ADR-0033), and Debian 13's own Python, which the runtime and the builder share (ADR-0013) and
# which moves only with the Debian major, a new decision.
STATED_ON_PURPOSE = {"1.34", "3.13"}


def sections(text: str) -> dict[str, str]:
    """The body of each `## ` section, by heading."""
    parts = re.split(r"^## (.+)$", text, flags=re.MULTILINE)
    return {head.strip(): body.strip() for head, body in zip(parts[1::2], parts[2::2], strict=True)}


def problems(name: str, text: str) -> list[str]:
    found = []
    if not re.match(rf"# ADR-{name[:4]}: \S", text):
        found.append(f"{name}: the title must be '# ADR-{name[:4]}: <title>'")
    body = sections(text)
    found += [f"{name}: no '## {s}' section" for s in SECTIONS if not body.get(s)]
    if body.get("Status") and not STATUS.match(body["Status"]):
        found.append(
            f"{name}: the status must be Accepted, Deferred or Superseded by [ADR-NNNN](...)"
        )
    if body.get("Date") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", body["Date"]):
        found.append(f"{name}: the date must be YYYY-MM-DD")
    return found


def pinned_versions() -> set[str]:
    """Each pinned version, and the minor of each three-part one, less those stated on purpose."""
    pins = [
        line.split("\t")[1].removeprefix("v")
        for line in (ROOT / "tools" / "pins.tsv").read_text("utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    kind = (ROOT / "tools" / "kind-nodes.tsv").read_text("utf-8")
    pins += re.findall(r"kindest/node:v([\d.]+)@", kind)
    minors = {m.group() for v in pins if (m := re.match(r"\d+\.\d+(?=\.\d+$)", v))}
    return (set(pins) | minors) - STATED_ON_PURPOSE


def stated_versions(text: str, versions: set[str]) -> list[str]:
    return sorted(v for v in versions if re.search(rf"(?<![\w.]){re.escape(v)}(?![\w]|\.\d)", text))


def test_the_minimum_set_exists() -> None:
    names = {p.name for p in ADRS}
    assert {f"{n}-{slug}.md" for n, slug in MINIMUM_SET.items()} <= names


@pytest.mark.parametrize("adr", ADRS, ids=lambda p: p.name)
def test_each_adr_has_the_sections_and_a_valid_status(adr: Path) -> None:
    assert problems(adr.name, adr.read_text("utf-8")) == []


@pytest.mark.parametrize("doc", [*ADRS, ADR_DIR / "README.md"], ids=lambda p: p.name)
def test_no_adr_restates_a_pinned_version(doc: Path) -> None:
    assert stated_versions(doc.read_text("utf-8"), pinned_versions()) == [], (
        f"{doc.name}: link tools/pins.tsv or tools/kind-nodes.tsv instead of the version"
    )


def test_the_template_has_the_sections() -> None:
    body = sections((ADR_DIR / "0000-template.md").read_text("utf-8"))
    assert all(s in body for s in SECTIONS)


def test_the_index_lists_every_adr_once_with_its_status() -> None:
    rows = INDEX_ROW.findall((ADR_DIR / "README.md").read_text("utf-8"))
    assert sorted(f for _, f, _ in rows) == [p.name for p in ADRS]
    for number, name, status in rows:
        assert name.startswith(f"{number}-")
        stated = sections((ADR_DIR / name).read_text("utf-8"))["Status"].split()[0].rstrip(".")
        assert status == stated, f"{name}: the index says {status}, the ADR {stated}"


def test_the_checks_fail_on_a_bad_adr() -> None:
    bad = "# ADR-0099: x\n\n## Status\n\nMaybe.\n\n## Date\n\nsoon\n\n## Context\n\nc\n"
    found = problems("0099-x.md", bad)
    assert any("status" in p for p in found)
    assert any("date" in p for p in found)
    assert any("'## Decision'" in p for p in found)
    assert problems("0098-x.md", bad.replace("0099", "0098").replace("Maybe.", "Deferred.")) != []


@pytest.mark.parametrize(
    "status",
    [
        "Accepted.",
        "Accepted (not yet implemented).",
        "Deferred. Revisit when x.",
        "Superseded by [ADR-0042](./0042-y.md).",
    ],
)
def test_each_status_form_passes(status: str) -> None:
    body = "\n\n".join(f"## {s}\n\n{'2026-01-02' if s == 'Date' else 'x'}" for s in SECTIONS)
    adr = "# ADR-0097: x\n\n" + body.replace("## Status\n\nx", f"## Status\n\n{status}")
    assert problems("0097-x.md", adr) == []


def test_a_superseded_row_parses_with_the_status_the_adr_states() -> None:
    row = "| [0097](./0097-x.md) | x | Superseded | replaced by 0098 |\n"
    assert INDEX_ROW.findall(row) == [("0097", "0097-x.md", "Superseded")]
    status = sections("## Status\n\nSuperseded by [ADR-0098](./0098-y.md).\n")["Status"]
    assert status.split()[0] == "Superseded"  # what the index test compares


def test_the_version_check_flags_a_pin_and_spares_the_floor() -> None:
    versions = {"3.1.4", "1.37", "1.20.0", "1.20"}
    assert stated_versions("cosign 3.1.4, Kyverno 1.20, kind (1.37)", versions) == [
        "1.20", "1.37", "3.1.4",
    ]  # fmt: skip
    assert stated_versions("backend:1.2.3, the floor 1.34, RFC 9745", versions) == []
    assert "1.34" not in pinned_versions()
