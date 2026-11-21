"""generate-vex.sh: Grype findings are `affected`; `not_affected` only from the reviewed list.

Offline: runs the script against a Grype JSON fixture written to tmp_path.
Spec: https://github.com/openvex/spec/blob/main/OPENVEX-SPEC.md
"""

import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO / "infra/security/generate-vex.sh"
LIST_FILE = REPO / "infra/security/local/vex-not-affected.json"
BASH: str = shutil.which("bash") or "/usr/bin/bash"
DIGEST = "sha256:" + "a" * 64
JUSTIFICATIONS = {
    "component_not_present",
    "vulnerable_code_not_present",
    "vulnerable_code_not_in_execute_path",
    "vulnerable_code_cannot_be_controlled_by_adversary",
    "inline_mitigations_already_exist",
}
# Git blob ids of the seven other frozen scripts; they were not touched by the VEX fix.
FROZEN_BLOBS = {
    "_helpers.sh": "6e0ba1f1e1881b7b7cd4b6d8bf202b73f3aced9a",
    "sbom-generate.sh": "35889cdaf07d3f95ac2a82e9ced6188481159e2f",
    "scan-vulnerabilities.sh": "9dd8bf309318cbff211f6ebb48ac06aa9b702382",
    "scan-licenses.sh": "a35e8dc10ae872be399dcb60f006e3f08b526a00",
    "check-ci-hardening.sh": "f9f1379cc54161970a43c9a42c2a62c0dc5c92de",
    "tools-install.sh": "e16fe9a9125c77bd0b0f0030f5e6772735e84d52",
    "verify-image-digest.sh": "0c7a9740a2e5b81eff18d6a2e486c733d3161d61",
}
TODAY = datetime.now(UTC).date()
FUTURE = (TODAY + timedelta(days=30)).isoformat()
PAST = (TODAY - timedelta(days=30)).isoformat()


def _match(vuln: str, pkg: str, fix: list[str]) -> dict[str, Any]:
    return {
        "vulnerability": {
            "id": vuln,
            "fix": {"versions": fix, "state": "fixed" if fix else "not-fixed"},
        },
        "artifact": {
            "name": pkg,
            "version": "1.0",
            "type": "python",
            "purl": f"pkg:pypi/{pkg}@1.0",
        },
    }


def _entry(**over: str) -> dict[str, Any]:
    e = {
        "vulnerability": "CVE-2026-0003",
        "service": "backend",
        "package": "libc",
        "justification": "vulnerable_code_not_in_execute_path",
        "impact_statement": "The affected function is never called.",
        "reviewed": TODAY.isoformat(),
        "until": FUTURE,
    }
    e.update(over)
    return e


def _run_full(
    tmp_path: Path,
    entries: list[Any] | None = None,
    raw_list: str | None = None,
    report: dict[str, Any] | None = None,
    raw_report: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, Any]]]:
    grype = tmp_path / "out/grype"
    grype.mkdir(parents=True)
    if report is None:
        report = {
            "matches": [
                _match("CVE-2026-0001", "openssl", ["1.1", "1.2"]),
                _match("CVE-2026-0002", "zlib", []),
                _match("CVE-2026-0003", "libc", ["2.0"]),
            ],
            "ignoredMatches": [_match("CVE-2026-0004", "node", ["22.1"])],
        }
    gj = grype / "backend.image.vuln.grype.json"
    gj.write_text(raw_report if raw_report is not None else json.dumps(report))
    (tmp_path / "digests.env").write_text(f"BACKEND_IMAGE_DIGEST={DIGEST}\n")
    (tmp_path / "scan.env").write_text("BACKEND_IMAGE=ghcr.io/o/r/backend:v1\n")
    lst = tmp_path / "list.json"
    lst.write_text(raw_list if raw_list is not None else json.dumps(entries or []))
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "LIB_TS": "0", "VEX_NOT_AFFECTED_LIST": str(lst)}
    env["PATH"] = f"{Path(shutil.which('jq') or '/usr/bin/jq').parent}:{env['PATH']}"
    out = tmp_path / "out"
    p = subprocess.run(
        [BASH, str(SCRIPT), str(out), str(tmp_path / "scan.env"), str(tmp_path / "digests.env")],
        capture_output=True, text=True, check=False, cwd=tmp_path, env=env,
    )  # fmt: skip
    doc = out / "vex/openvex.json"
    return p, (json.loads(doc.read_text())["statements"] if doc.exists() else [])


def _run(
    tmp_path: Path, entries: list[Any] | None = None, raw_list: str | None = None
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    p, stmts = _run_full(tmp_path, entries, raw_list)
    return p, {s["vulnerability_id"]: s for s in stmts}


def test_findings_are_affected_with_action_statements(tmp_path: Path) -> None:
    p, s = _run(tmp_path)
    assert p.returncode == 0, p.stderr
    assert set(s) == {"CVE-2026-0001", "CVE-2026-0002", "CVE-2026-0003", "CVE-2026-0004"}
    assert all(x["status"] == "affected" for x in s.values())
    assert s["CVE-2026-0001"]["action_statement"] == "Upgrade openssl to 1.1 or later."
    assert s["CVE-2026-0002"]["action_statement"] == (
        "No upstream fix is available yet; the scheduled re-scan tracks it."
    )
    assert s["CVE-2026-0004"]["status"] == "affected"  # an ignoredMatch is still present
    assert s["CVE-2026-0001"]["@id"] == "https://osv.dev/vulnerability/CVE-2026-0001"
    assert s["CVE-2026-0001"]["products"][0]["@id"] == (
        f"pkg:oci/backend@{DIGEST}?repository_url=o/r/backend&tag=v1"
    )
    assert s["CVE-2026-0001"]["products"][0]["subcomponents"][0]["@id"] == "pkg:pypi/openssl@1.0"


def test_every_statement_is_valid_and_never_fixed_version(tmp_path: Path) -> None:
    _, s = _run(tmp_path, [_entry()])
    assert s
    for x in s.values():
        if x["status"] == "affected":
            assert x["action_statement"]
        else:
            assert x["justification"] in JUSTIFICATIONS
        assert "fixed_version" not in json.dumps(x)


def test_live_entry_gives_not_affected(tmp_path: Path) -> None:
    p, s = _run(tmp_path, [_entry()])
    assert p.returncode == 0, p.stderr
    x = s["CVE-2026-0003"]
    assert x["status"] == "not_affected"
    assert x["justification"] == "vulnerable_code_not_in_execute_path"
    assert x["impact_statement"] == "The affected function is never called."
    assert s["CVE-2026-0001"]["status"] == "affected"


def test_entry_for_another_service_or_package_does_not_match(tmp_path: Path) -> None:
    p, s = _run(tmp_path, [_entry(service="frontend"), _entry(package="other")])
    assert p.returncode == 0
    assert s["CVE-2026-0003"]["status"] == "affected"


def test_expired_entry_is_ignored_with_a_warning(tmp_path: Path) -> None:
    p, s = _run(tmp_path, [_entry(reviewed=PAST, until=PAST)])
    assert p.returncode == 0
    assert s["CVE-2026-0003"]["status"] == "affected"
    assert "expired" in p.stderr


def test_unmatched_entry_produces_nothing(tmp_path: Path) -> None:
    p, s = _run(tmp_path, [_entry(vulnerability="CVE-2099-9999")])
    assert p.returncode == 0
    assert "CVE-2099-9999" not in s
    assert len(s) == 4  # noqa: PLR2004
    assert "matched no finding" in p.stdout


REQUIRED = ("vulnerability", "service", "package", "justification", "impact_statement")
BAD_ENTRIES: list[Any] = [
    _entry(justification="fixed_version"),
    _entry(impact_statement=""),
    _entry(impact_statement="  \t "),
    _entry(until="2026-13-45"),
    _entry(reviewed="yesterday"),
    _entry(reviewed=FUTURE, until=FUTURE),
    _entry(reviewed=TODAY.isoformat(), until=PAST),
    "not-an-object",
    *({k: v for k, v in _entry().items() if k != key} for key in REQUIRED),
    *(_entry(**{key: "   "}) for key in REQUIRED),
]


@pytest.mark.parametrize("bad", BAD_ENTRIES)
def test_invalid_entry_fails(tmp_path: Path, bad: Any) -> None:  # noqa: ANN401
    p, _ = _run(tmp_path, [bad])
    assert p.returncode != 0
    assert "invalid not_affected list" in p.stderr


def test_duplicate_entries_fail_naming_the_vulnerability(tmp_path: Path) -> None:
    p, _ = _run(tmp_path, [_entry(), _entry(impact_statement="Other text.")])
    assert p.returncode != 0
    assert "duplicate entry for CVE-2026-0003" in p.stderr


@pytest.mark.parametrize("raw", ["", "{}", "null", "[] []", "[] [{}]", "[", "7"])
def test_list_must_be_exactly_one_json_array(tmp_path: Path, raw: str) -> None:
    p, _ = _run(tmp_path, raw_list=raw)
    assert p.returncode != 0
    assert not (tmp_path / "out/vex/openvex.json").exists()


def test_until_is_inclusive(tmp_path: Path) -> None:
    today = TODAY.isoformat()
    p, s = _run(tmp_path, [_entry(until=today)])
    assert p.returncode == 0, p.stderr
    assert s["CVE-2026-0003"]["status"] == "not_affected"  # valid through the until day
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    p, s = _run(tmp_path / "y", [_entry(reviewed=yesterday, until=yesterday)])
    assert s["CVE-2026-0003"]["status"] == "affected"
    assert "expired" in p.stderr


@pytest.mark.parametrize("raw", ['{"matches":[', ""], ids=["truncated", "zero-bytes"])
def test_malformed_grype_report_fails_closed(tmp_path: Path, raw: str) -> None:
    p, stmts = _run_full(tmp_path, raw_report=raw)
    assert p.returncode != 0
    assert "cannot build VEX statements from" in p.stderr
    assert "backend.image.vuln.grype.json" in p.stderr
    assert not stmts


def test_non_string_fix_version_fails_closed(tmp_path: Path) -> None:
    bad = _match("CVE-2026-0005", "pkg", [])
    bad["vulnerability"]["fix"]["versions"] = [7]
    p, _ = _run_full(tmp_path, report={"matches": [bad]})
    assert p.returncode != 0
    assert "cannot build VEX statements from" in p.stderr


def test_duplicate_findings_collapse_to_one_statement(tmp_path: Path) -> None:
    m = _match("CVE-2026-0001", "openssl", ["1.1"])
    p, stmts = _run_full(tmp_path, report={"matches": [m, m], "ignoredMatches": [m]})
    assert p.returncode == 0, p.stderr
    assert len(stmts) == 1


def test_different_subcomponents_stay_separate_statements(tmp_path: Path) -> None:
    a, b = _match("CVE-2026-0001", "openssl", ["1.1"]), _match("CVE-2026-0001", "zlib", ["1.1"])
    p, stmts = _run_full(tmp_path, report={"matches": [a, b]})
    assert p.returncode == 0, p.stderr
    assert len(stmts) == 2  # noqa: PLR2004


@pytest.mark.parametrize(
    ("artifact", "expected"),
    [
        ({"purl": "pkg:npm/x@1"}, "Upgrade pkg:npm/x@1 to 2 or later."),
        ({"name": "  ", "purl": "pkg:npm/x@1"}, "Upgrade pkg:npm/x@1 to 2 or later."),
        ({}, "Upgrade the affected package to 2 or later."),
    ],
)
def test_action_text_without_a_package_name(
    tmp_path: Path, artifact: dict[str, Any], expected: str
) -> None:
    m = _match("CVE-2026-0001", "x", ["2"])
    m["artifact"] = artifact
    p, stmts = _run_full(tmp_path, report={"matches": [m]})
    assert p.returncode == 0, p.stderr
    assert stmts[0]["action_statement"] == expected


def test_default_list_is_an_empty_json_array() -> None:
    assert json.loads(LIST_FILE.read_text()) == []


def test_other_frozen_scripts_are_unchanged() -> None:
    for name, blob in FROZEN_BLOBS.items():
        data = (REPO / "infra/security" / name).read_bytes()
        got = hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()
        assert got == blob, f"{name} is frozen"


@pytest.mark.parametrize(
    "raw",
    ["null", '"x"', "[]", "{}", '{"matches":"x"}', '{"matches":{}}',
     '{"matches":[],"ignoredMatches":"x"}', '{"matches":[1]}',
     '{"matches":"x"} {"matches":[]}', '{"matches":[]} {"matches":"x"}',
     '{"matches":[]} {"matches":[]}',
     '{"matches":[],"ignoredMatches":false}', '{"matches":[],"ignoredMatches":0}',
     '{"matches":[],"ignoredMatches":""}', '{"matches":[],"ignoredMatches":{}}'],
)  # fmt: skip
def test_report_of_the_wrong_shape_fails_closed(tmp_path: Path, raw: str) -> None:
    p, stmts = _run_full(tmp_path, raw_report=raw)
    assert p.returncode != 0
    assert "cannot build VEX statements from" in p.stderr
    assert "not a Grype JSON report" in p.stderr
    assert not stmts


@pytest.mark.parametrize("raw", ['{"matches":[]}', '{"matches":[],"ignoredMatches":null}'])
def test_report_with_no_findings_is_valid(tmp_path: Path, raw: str) -> None:
    p, stmts = _run_full(tmp_path, raw_report=raw)
    assert p.returncode == 0, p.stderr
    assert stmts == []


def test_multiline_impact_statement_with_a_blank_line_is_accepted(tmp_path: Path) -> None:
    p, s = _run(tmp_path, [_entry(impact_statement="First paragraph.\n\nSecond paragraph.")])
    assert p.returncode == 0, p.stderr
    assert s["CVE-2026-0003"]["status"] == "not_affected"


def test_whitespace_and_newlines_only_impact_statement_is_rejected(tmp_path: Path) -> None:
    p, _ = _run(tmp_path, [_entry(impact_statement=" \n\n\t\n")])
    assert p.returncode != 0
    assert "invalid not_affected list" in p.stderr


def test_a_report_larger_than_one_command_line_argument_still_renders(tmp_path: Path) -> None:
    # Linux caps one argv string at 128 KiB (MAX_ARG_STRLEN), so jq must read scanner-derived JSON
    # from files or stdin, never from --argjson. A real image report easily outgrows that.
    n = 1500
    report = {
        "matches": [_match(f"CVE-2026-{10000 + i}", f"package-{i}", ["9.9"]) for i in range(n)],
        "ignoredMatches": [],
    }
    p, stmts = _run_full(tmp_path, report=report)
    assert p.returncode == 0, p.stderr
    assert len(stmts) == n
    assert len(json.dumps(stmts)) > 128 * 1024
