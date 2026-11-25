"""scripts/drift_report.py: one `drift` issue per environment, updated in place (gh stubbed)."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

from tests.scripts.test_rescan_report import Gh

if TYPE_CHECKING:
    import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _mod() -> ModuleType:
    sys.path.insert(0, str(SCRIPTS))
    try:
        return importlib.import_module("drift_report")
    finally:
        sys.path.remove(str(SCRIPTS))


def _finding(
    name: str = "svc-config", path: str = "data.A", change: str = "changed"
) -> dict[str, str]:
    return {
        "kind": "ConfigMap",
        "namespace": "svc-dev",
        "name": name,
        "path": path,
        "change": change,
    }


def _setup(
    tmp_path: Path, findings: dict[str, list[dict[str, str]]], skip: dict[str, str] | None = None
) -> Path:
    deployments: dict[str, Any] = {
        e: {"sha": "a" * 40, "ref": "main", "digests": {}} for e in findings
    }
    deployments |= {e: {"skip": r} for e, r in (skip or {}).items()}
    (tmp_path / "findings").mkdir()
    for env, found in findings.items():
        report = {"environment": env, "release": "svc", "findings": found, "notes": []}
        (tmp_path / "findings" / f"{env}.json").write_text(json.dumps(report))
    path = tmp_path / "deployments.json"
    path.write_text(json.dumps(deployments))
    return path


def _report(gh: Gh, deployments: Path, tmp_path: Path) -> list[str]:
    return list(_mod().report(gh, json.loads(deployments.read_text()), tmp_path / "findings"))


def test_findings_open_one_labelled_issue_per_environment(tmp_path: Path) -> None:
    d = _setup(tmp_path, {"dev": [_finding(), _finding("svc-x", "", "extra")], "qa": [_finding()]})
    gh = Gh([], [])
    notes = _report(gh, d, tmp_path)
    assert gh.verbs() == ["label create", "issue create", "issue create"]
    _, args, body = gh.calls[1]
    assert args[args.index("--title") + 1] == "Live drift in dev"
    assert args[args.index("--label") + 1] == "drift"
    assert "<!-- drift-env: dev -->" in body
    assert "| changed | ConfigMap | `svc-dev` | `svc-config` | `data.A` |" in body
    assert "| extra | ConfigMap | `svc-dev` | `svc-x` | the object |" in body
    assert "never closed automatically" in body
    assert "opened: dev (2 findings)" in notes


def test_an_unchanged_state_does_nothing_and_a_changed_one_edits_and_comments(
    tmp_path: Path,
) -> None:
    m = _mod()
    d = _setup(tmp_path, {"dev": [_finding()]})
    body = m.body_of("dev", [_finding()])
    gh = Gh(["drift"], [{"number": 4, "body": body}])
    assert _report(gh, d, tmp_path) == ["unchanged: #4 dev"]
    assert gh.verbs() == []
    (tmp_path / "later").mkdir()
    d = _setup(tmp_path / "later", {"dev": [_finding(), _finding("svc-y", "", "missing")]})
    gh = Gh(["drift"], [{"number": 4, "body": body}])
    assert _report(gh, d, tmp_path / "later") == ["updated: #4 dev (2 findings)"]
    assert gh.verbs() == ["issue edit", "issue comment"]
    assert "--app" in gh.list_args  # only the workflow's own issues count


def test_no_drift_leaves_an_open_issue_open(tmp_path: Path) -> None:
    m = _mod()
    d = _setup(tmp_path, {"dev": []})
    gh = Gh(["drift"], [{"number": 4, "body": m.body_of("dev", [_finding()])}])
    notes = _report(gh, d, tmp_path)
    assert gh.verbs() == []
    assert notes == ["dev: no drift now; #4 stays open until someone closes it"]


def test_untrusted_names_are_sanitised(tmp_path: Path) -> None:
    d = _setup(tmp_path, {"dev": [_finding("x`| @team <b>", "data.`y`")]})
    gh = Gh(["drift"], [])
    _report(gh, d, tmp_path)
    body = gh.calls[0][2]
    assert "@team" not in body.replace("_@team", "")
    assert "<b>" not in body
    assert "`|" not in body


def test_a_skipped_environment_and_a_missing_report_are_said_in_the_summary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d = _setup(tmp_path, {}, skip={"production": "no successful deployment"})
    deployments = json.loads(d.read_text()) | {
        "qa": {"sha": "a" * 40, "ref": "main", "digests": {}}
    }
    d.write_text(json.dumps(deployments))
    monkeypatch.setenv("GH_REPO", "o/r")
    assert _mod().main(["x", str(d), str(tmp_path / "findings")], Gh([], [])) == 0
    out = capsys.readouterr().out
    assert "- production: not compared: no successful deployment" in out
    assert "- qa: no report (the drift job skipped or failed; see its summary)" in out
