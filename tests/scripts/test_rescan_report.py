"""scripts/rescan_report.py: one issue per (vulnerability, package), deduplicated (gh stubbed)."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64
MARKERS = 2


def _mod() -> ModuleType:
    sys.path.insert(0, str(SCRIPTS))
    try:
        return importlib.import_module("rescan_report")
    finally:
        sys.path.remove(str(SCRIPTS))


def _match(vid: str, sev: str, pkg: str, version: str = "1.0", fixed: str | None = None) -> dict:  # type: ignore[type-arg]
    fix = {"versions": [fixed]} if fixed else {"versions": []}
    return {
        "vulnerability": {"id": vid, "severity": sev, "fix": fix},
        "artifact": {"name": pkg, "version": version},
    }


def _setup(tmp_path: Path, reports: dict[str, list[dict]], where: dict[str, list[str]]) -> Path:  # type: ignore[type-arg]
    """reports: short -> matches. Writes digests.json and the findings dir; returns digests.json."""
    images = [
        {"id": "backend", "digest": f"sha256:{s * 64}", "short": s * 12, "where": where[s]}
        for s in reports
    ]
    (tmp_path / "findings").mkdir()
    for s, matches in reports.items():
        (tmp_path / "findings" / f"{s * 12}.json").write_text(json.dumps({"matches": matches}))
    path = tmp_path / "digests.json"
    path.write_text(json.dumps({"images": images, "unscannable": []}))
    return path


class Gh:
    def __init__(self, labels: list[str], issues: list[dict]) -> None:  # type: ignore[type-arg]
        self.labels, self.issues = labels, issues
        self.calls: list[tuple[str, list[str], str]] = []

    def __call__(self, args: list[str]) -> bytes:
        if args[:2] == ["label", "list"]:
            return json.dumps([{"name": n} for n in self.labels]).encode()
        if args[:2] == ["issue", "list"]:
            return json.dumps(self.issues).encode()
        body = (
            Path(args[args.index("--body-file") + 1]).read_text() if "--body-file" in args else ""
        )
        self.calls.append((" ".join(args[:2]), args, body))
        return b""

    def verbs(self) -> list[str]:
        return [c[0] for c in self.calls]


def _run(m: ModuleType, gh: Gh, digests: Path, tmp_path: Path) -> list[str]:
    notes, _ = m.report(gh, json.loads(digests.read_text()), tmp_path / "findings")
    return list(notes)


def test_a_new_critical_finding_opens_one_labelled_issue_with_the_marker(tmp_path: Path) -> None:
    m = _mod()
    d = _setup(tmp_path, {"1": [_match("CVE-1", "Critical", "libx", "1.0", "1.1")]}, {"1": ["dev"]})
    gh = Gh(["vuln"], [])
    _run(m, gh, d, tmp_path)
    assert gh.verbs() == ["issue create"]
    _, args, body = gh.calls[0]
    assert args[args.index("--label") + 1] == "vuln"
    assert args[args.index("--title") + 1] == "CVE-1 in libx"
    assert "<!-- vuln-key: CVE-1|libx -->" in body
    assert D1 in body
    assert "7 days" in body
    assert "1.1" in body


def test_a_high_finding_names_the_30_day_target(tmp_path: Path) -> None:
    m = _mod()
    d = _setup(tmp_path, {"1": [_match("CVE-2", "High", "liby")]}, {"1": ["qa"]})
    gh = Gh(["vuln"], [])
    _run(m, gh, d, tmp_path)
    assert "30 days" in gh.calls[0][2]


def test_medium_and_low_findings_open_nothing_and_are_counted(tmp_path: Path) -> None:
    m = _mod()
    d = _setup(
        tmp_path,
        {
            "1": [
                _match("CVE-3", "Medium", "a"),
                _match("CVE-4", "Low", "b"),
                _match("X", "Low", "c"),
            ]
        },
        {"1": ["dev"]},
    )
    gh = Gh(["vuln"], [])
    _, other = m.report(gh, json.loads(d.read_text()), tmp_path / "findings")
    assert gh.calls == []
    assert dict(other) == {"Medium": 1, "Low": 2}


def test_the_label_is_created_only_when_absent(tmp_path: Path) -> None:
    m = _mod()
    d = _setup(tmp_path, {"1": [_match("CVE-1", "High", "p")]}, {"1": ["dev"]})
    absent = Gh([], [])
    _run(m, absent, d, tmp_path)
    assert absent.verbs() == ["label create", "issue create"]
    present = Gh(["vuln", "other"], [])
    _run(m, present, d, tmp_path)
    assert "label create" not in present.verbs()


def test_an_unchanged_finding_does_nothing_the_second_time(tmp_path: Path) -> None:
    m = _mod()
    d = _setup(tmp_path, {"1": [_match("CVE-1", "High", "p")]}, {"1": ["dev"]})
    first = Gh(["vuln"], [])
    _run(m, first, d, tmp_path)
    existing = [{"number": 12, "body": first.calls[0][2]}]
    second = Gh(["vuln"], existing)
    notes = _run(m, second, d, tmp_path)
    assert second.calls == []
    assert notes == ["unchanged: #12 CVE-1|p"]


@pytest.mark.parametrize(
    ("severity", "where", "reports"),
    [
        ("Critical", {"1": ["dev"]}, {"1": 0}),  # severity rose
        ("High", {"1": ["dev", "qa"]}, {"1": 0}),  # runs in one more environment
        ("High", {"1": ["dev"], "2": ["dev"]}, {"1": 0, "2": 0}),  # a second digest is affected
    ],
)
def test_a_changed_severity_or_affected_set_edits_the_issue_and_comments(
    tmp_path: Path, severity: str, where: dict[str, list[str]], reports: dict[str, int]
) -> None:
    m = _mod()
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    old = _setup(old_dir, {"1": [_match("CVE-1", "High", "p")]}, {"1": ["dev"]})
    first = Gh(["vuln"], [])
    _run(m, first, old, old_dir)
    new = _setup(tmp_path, {s: [_match("CVE-1", severity, "p")] for s in reports}, where)
    second = Gh(["vuln"], [{"number": 5, "body": first.calls[0][2]}])
    notes = _run(m, second, new, tmp_path)
    assert second.verbs() == ["issue edit", "issue comment"]
    assert second.calls[0][1][2] == "5"
    assert second.calls[1][1][2] == "5"
    assert notes[0].startswith("updated: #5")


def test_two_digests_with_the_same_finding_share_one_issue(tmp_path: Path) -> None:
    m = _mod()
    d = _setup(
        tmp_path,
        {"1": [_match("CVE-1", "High", "p")], "2": [_match("CVE-1", "High", "p")]},
        {"1": ["dev"], "2": ["release v1"]},
    )
    gh = Gh(["vuln"], [])
    _run(m, gh, d, tmp_path)
    assert gh.verbs() == ["issue create"]
    assert D1 in gh.calls[0][2]
    assert D2 in gh.calls[0][2]


def test_the_oldest_open_issue_wins_when_a_key_is_duplicated(tmp_path: Path) -> None:
    m = _mod()
    d = _setup(tmp_path, {"1": [_match("CVE-1", "High", "p")]}, {"1": ["dev"]})
    first = Gh(["vuln"], [])
    _run(m, first, d, tmp_path)
    body = first.calls[0][2]
    gh = Gh(["vuln"], [{"number": 9, "body": body}, {"number": 3, "body": body}])
    assert _run(m, gh, d, tmp_path) == ["unchanged: #3 CVE-1|p"]


def test_hostile_text_cannot_break_out_of_the_body_or_the_marker(tmp_path: Path) -> None:
    m = _mod()
    nasty = "x --> <script>`$(id)`\n# injected"
    d = _setup(tmp_path, {"1": [_match("CVE-1", "High", nasty, nasty)]}, {"1": ["dev"]})
    gh = Gh(["vuln"], [])
    _run(m, gh, d, tmp_path)
    _, args, body = gh.calls[0]
    assert "<script>" not in body
    assert "$(" not in body
    assert body.count("-->") == MARKERS  # only our two markers
    assert "\n# injected" not in body
    assert "<" not in args[args.index("--title") + 1]


def test_a_failing_gh_call_is_an_error(tmp_path: Path) -> None:
    m = _mod()
    d = _setup(tmp_path, {"1": [_match("CVE-1", "High", "p")]}, {"1": ["dev"]})

    def boom(args: list[str]) -> bytes:
        msg = f"gh {args[0]} failed"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError):
        m.report(boom, json.loads(d.read_text()), tmp_path / "findings")


def test_main_puts_unscannable_digests_and_other_severities_in_the_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    m = _mod()
    d = _setup(tmp_path, {"1": [_match("CVE-3", "Medium", "a")]}, {"1": ["dev"]})
    doc = json.loads(d.read_text())
    doc["unscannable"] = [
        {"where": "qa", "reason": "not scannable: digest artifact expired (redeploy)"}
    ]
    d.write_text(json.dumps(doc))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert m.main(["x", str(d), str(tmp_path / "findings")], Gh(["vuln"], [])) == 0
    text = summary.read_text()
    assert "Medium: 1" in text
    assert "qa: not scannable: digest artifact expired (redeploy)" in text
