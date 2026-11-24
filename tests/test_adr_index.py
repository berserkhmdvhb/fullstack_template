# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Every ADR in wiki/adr has the required sections and a valid status, and the index lists each.

The seven ADRs of the minimum set (GitHub only to IaC placement) must at least exist."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ADR_DIR = Path(__file__).resolve().parents[1] / "wiki" / "adr"
ADRS = sorted(
    p for p in ADR_DIR.glob("[0-9][0-9][0-9][0-9]-*.md") if not p.name.startswith("0000-")
)
MINIMUM = 7
SECTIONS = (
    "Status", "Date", "Context", "Decision", "Consequences", "Alternatives considered", "Links",
)  # fmt: skip
STATUS = re.compile(r"(Accepted|Deferred|Superseded by \[ADR-\d{4}\]\([^)]+\))\b")


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


def test_there_are_adrs() -> None:
    assert len(ADRS) >= MINIMUM


@pytest.mark.parametrize("adr", ADRS, ids=lambda p: p.name)
def test_each_adr_has_the_sections_and_a_valid_status(adr: Path) -> None:
    assert problems(adr.name, adr.read_text("utf-8")) == []


def test_the_template_has_the_sections() -> None:
    body = sections((ADR_DIR / "0000-template.md").read_text("utf-8"))
    assert all(s in body for s in SECTIONS)


def test_the_index_lists_every_adr_once_with_its_status() -> None:
    index = (ADR_DIR / "README.md").read_text("utf-8")
    rows = re.findall(r"^\| \[(\d{4})\]\(\./([^)]+)\) \| [^|]+ \| (\w+) \|", index, re.MULTILINE)
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
