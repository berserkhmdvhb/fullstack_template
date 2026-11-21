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


def _run(
    tmp_path: Path, entries: list[Any] | None = None, raw_list: str | None = None
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    grype = tmp_path / "out/grype"
    grype.mkdir(parents=True)
    report = {
        "matches": [
            _match("CVE-2026-0001", "openssl", ["1.1", "1.2"]),
            _match("CVE-2026-0002", "zlib", []),
            _match("CVE-2026-0003", "libc", ["2.0"]),
        ],
        "ignoredMatches": [_match("CVE-2026-0004", "node", ["22.1"])],
    }
    (grype / "backend.image.vuln.grype.json").write_text(json.dumps(report))
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
    stmts = (
        {s["vulnerability_id"]: s for s in json.loads(doc.read_text())["statements"]}
        if doc.exists()
        else {}
    )
    return p, stmts


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
    assert s["CVE-2026-0001"]["products"][0]["@id"].startswith(f"pkg:oci/backend@{DIGEST}?")
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
    p, s = _run(tmp_path, [_entry(until=PAST)])
    assert p.returncode == 0
    assert s["CVE-2026-0003"]["status"] == "affected"
    assert "expired" in p.stderr


def test_unmatched_entry_produces_nothing(tmp_path: Path) -> None:
    p, s = _run(tmp_path, [_entry(vulnerability="CVE-2099-9999")])
    assert p.returncode == 0
    assert "CVE-2099-9999" not in s
    assert len(s) == 4  # noqa: PLR2004
    assert "matched no finding" in p.stdout


@pytest.mark.parametrize(
    "bad",
    [
        _entry(justification="fixed_version"),
        _entry(impact_statement=""),
        _entry(until="2026-13-45"),
        _entry(reviewed="yesterday"),
        {k: v for k, v in _entry().items() if k != "package"},
    ],
    ids=["justification", "impact", "until", "reviewed", "package-missing"],
)
def test_invalid_entry_fails(tmp_path: Path, bad: dict[str, Any]) -> None:
    p, _ = _run(tmp_path, [bad])
    assert p.returncode != 0
    assert "invalid not_affected list" in p.stderr


def test_non_array_list_fails(tmp_path: Path) -> None:
    p, _ = _run(tmp_path, raw_list="{}")
    assert p.returncode != 0


def test_default_list_is_an_empty_json_array() -> None:
    assert json.loads(LIST_FILE.read_text()) == []


def test_other_frozen_scripts_are_unchanged() -> None:
    for name, blob in FROZEN_BLOBS.items():
        data = (REPO / "infra/security" / name).read_bytes()
        got = hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()
        assert got == blob, f"{name} is frozen"
