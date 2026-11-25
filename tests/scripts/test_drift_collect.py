"""scripts/drift_collect.py: what each environment's deployment applied (GitHub API stubbed)."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.scripts.test_rescan_collect import (
    D1,
    D2,
    OPS_PATH,
    RELEASE_PATH,
    REPO,
    URL,
    Api,
    _digests,
)
from tests.scripts.test_rescan_collect import api as api  # noqa: PLC0414 - the shared fixture

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
SHA = "a" * 40


def _mod() -> ModuleType:
    sys.path.insert(0, str(SCRIPTS))
    try:
        return importlib.import_module("drift_collect")
    finally:
        sys.path.remove(str(SCRIPTS))


def _release(api: Api, env: str, ref: str = "main", sha: str = SHA, digest: str = D1) -> None:
    api.deploy(env, (7, ref, sha, f"{URL}/100/job/1001"))
    api.run(100, RELEASE_PATH)
    api.artifact(100, 1000, _digests(digest))


def test_a_release_deployment_gives_its_commit_ref_and_digests(api: Api) -> None:
    _release(api, "dev")
    out = _mod().collect(api, REPO)
    assert out["dev"] == {"sha": SHA, "ref": "main", "digests": {"BACKEND_IMAGE_DIGEST": D1}}
    assert out["qa"] == {"skip": "no successful deployment"}
    assert set(out) == {"dev", "qa", "production"}


def test_a_rollback_is_not_compared(api: Api) -> None:
    api.deploy("production", (9, "main", SHA, f"{URL}/200/job/2"))
    api.run(200, OPS_PATH)
    assert "rollback" in _mod().collect(api, REPO)["production"]["skip"]


def test_an_expired_digest_artifact_is_not_compared(api: Api) -> None:
    api.deploy("dev", (7, "main", SHA, f"{URL}/100/job/1001"))
    api.run(100, RELEASE_PATH)
    api.artifact(100, 1000, _digests(D2), expired=True)
    assert "expired" in _mod().collect(api, REPO)["dev"]["skip"]


@pytest.mark.parametrize(("ref", "sha"), [("main", "abc"), ("feature/x", SHA), ("main;x", SHA)])
def test_an_unusable_ref_or_commit_is_not_compared(api: Api, ref: str, sha: str) -> None:
    _release(api, "dev", ref=ref, sha=sha)
    assert "unusable" in _mod().collect(api, REPO)["dev"]["skip"]


def test_a_failing_api_call_fails_the_collection() -> None:
    def boom(args: list[str]) -> bytes:
        msg = f"gh {args[1]} failed"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError):
        _mod().collect(boom, REPO)


def test_main_writes_deployments_json_and_a_summary(
    api: Api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _release(api, "dev", ref="v1.2.3")
    api.routes[f"repos/{REPO}/releases/tags/"] = _mod().NotFoundError("HTTP 404")
    monkeypatch.setenv("GH_REPO", REPO)
    assert _mod().main(["x", str(tmp_path)], api) == 0
    out = json.loads((tmp_path / "deployments.json").read_text())
    assert out["dev"]["ref"] == "v1.2.3"
    printed = capsys.readouterr().out
    assert f"- dev: `v1.2.3` at `{SHA[:12]}`" in printed
    assert "- qa: not compared: no successful deployment" in printed


def test_a_drift_environments_record_is_never_read_as_a_deployment(api: Api) -> None:
    """drift.yml declares `deployment: false`, but even a record in a `drift-<env>` environment is
    never one of dev's, qa's or production's: the lookup asks for each environment by exact name."""
    from tests.scripts.test_rescan_collect import _mod as rescan  # noqa: PLC0415

    for env in ("dev", "qa", "production"):
        api.routes[f"repos/{REPO}/deployments?environment=drift-{env}"] = [
            {"id": 99, "ref": "main", "sha": SHA}
        ]
    out = _mod().collect(api, REPO)
    assert all(d == {"skip": "no successful deployment"} for d in out.values())
    images, bad, _ = rescan().collect(api, REPO)
    assert (images, bad) == ({}, [])
    assert not [p for p in api.paths if "drift-" in p]
    assert rescan().ENVIRONMENTS == ("dev", "qa", "production")


def test_a_run_on_another_commit_is_not_compared(api: Api) -> None:
    api.deploy("dev", (7, "main", SHA, f"{URL}/100/job/1001"))
    api.run(100, RELEASE_PATH, head=("b" * 40, "main"))
    api.artifact(100, 1000, _digests(D1))
    assert "is not the deployment's" in _mod().collect(api, REPO)["dev"]["skip"]
