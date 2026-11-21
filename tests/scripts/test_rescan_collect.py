"""scripts/rescan_collect.py: which digests the weekly re-scan looks at (GitHub API stubbed)."""

from __future__ import annotations

import importlib
import io
import json
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64
D3 = "sha256:" + "3" * 64
REPO = "o/r"
EXPIRED = "not scannable: digest artifact expired (redeploy)"
ROLLBACK = "after rollback: running digest unknown"
RELEASE_PATH = ".github/workflows/release.yml"
OPS_PATH = ".github/workflows/deploy-ops.yml"
T0, T1, T2, T3 = (f"2026-11-0{n}T10:00:00Z" for n in (1, 2, 3, 4))
URL = f"https://github.com/{REPO}/actions/runs"


def _mod() -> ModuleType:
    sys.path.insert(0, str(SCRIPTS))
    try:
        return importlib.import_module("rescan_collect")
    finally:
        sys.path.remove(str(SCRIPTS))


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


def _digests(digest: str) -> dict[str, str]:
    return {
        "image-digests.env": f"BACKEND_IMAGE_DIGEST={digest}\n",
        "scan.env": "BACKEND_IMAGE=ghcr.io/o/r/backend:1.0.0-rel-abc\n",
    }


class Api:
    """A fake `gh`: routes `gh api <path>` (longest prefix first) to JSON or bytes."""

    def __init__(self) -> None:
        self.routes: dict[str, object] = {}
        self.paths: list[str] = []

    def __call__(self, args: list[str]) -> bytes:
        assert args[0] == "api", args
        path = args[-1]
        self.paths.append(path)
        hits = [p for p in self.routes if path.startswith(p)]
        if not hits:
            msg = f"unrouted: {path}"
            raise AssertionError(msg)
        value = self.routes[max(hits, key=len)]
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, bytes) else json.dumps(value).encode()

    def deploy(self, env: str, *deps: tuple[int, str, str, str]) -> None:
        """deps: (id, ref, sha, status url or 'failure'), newest first."""
        self.routes[f"repos/{REPO}/deployments?environment={env}"] = [
            {"id": i, "ref": ref, "sha": sha} for i, ref, sha, _ in deps
        ]
        for i, _, _, url in deps:
            status = {"state": "failure"} if url == "failure" else {"state": "success"}
            if url != "failure":
                status["log_url"] = url
            self.routes[f"repos/{REPO}/deployments/{i}/statuses"] = [status]

    def run(
        self, run_id: int, attempt: int | None, path: str, window: tuple[str, str] = (T0, T3)
    ) -> None:
        base = f"repos/{REPO}/actions/runs/{run_id}"
        self.routes[base + (f"/attempts/{attempt}" if attempt else "")] = {
            "path": path,
            "run_started_at": window[0],
            "updated_at": window[1],
        }

    def jobs(self, run_id: int, attempt: int | None, *names: str) -> None:
        base = f"repos/{REPO}/actions/runs/{run_id}"
        self.routes[base + (f"/attempts/{attempt}" if attempt else "") + "/jobs"] = {
            "jobs": [{"name": n} for n in names]
        }

    def artifact(
        self,
        run_id: int,
        aid: int,
        files: dict[str, str],
        *,
        created: str = T1,
        expired: bool = False,
    ) -> None:
        """Add one `image-digests` artifact to the run's list (a re-run adds another)."""
        key = f"repos/{REPO}/actions/runs/{run_id}/artifacts?name=image-digests"
        listing = self.routes.setdefault(key, {"artifacts": []})
        assert isinstance(listing, dict)
        listing["artifacts"].append({"id": aid, "expired": expired, "created_at": created})
        self.routes[f"repos/{REPO}/actions/artifacts/{aid}/zip"] = _zip(files)

    def no_release(self) -> None:
        self.routes[f"repos/{REPO}/releases/latest"] = _mod().NotFoundError("HTTP 404")


@pytest.fixture
def api() -> Api:
    a = Api()
    a.no_release()
    a.routes[f"repos/{REPO}/releases/tags/"] = _mod().NotFoundError("HTTP 404")
    for env in ("dev", "qa", "production"):
        a.routes[f"repos/{REPO}/deployments?environment={env}"] = []
    return a


def test_a_deployment_is_mapped_through_its_status_url_to_the_release_run_artifact(
    api: Api,
) -> None:
    m = _mod()
    api.deploy("dev", (7, "main", "aaa", f"{URL}/100"))
    api.run(100, None, RELEASE_PATH)
    api.artifact(100, 1000, _digests(D1))
    images, bad, notes = m.collect(api, REPO)
    assert bad == []
    assert images[D1]["where"] == ["dev"]
    assert images[D1]["repository"] == "ghcr.io/o/r/backend"
    assert images[D1]["id"] == "backend"
    assert "qa: no successful release deployment" in notes
    assert not any("head_sha" in p for p in api.paths)  # the sha is not used to guess the run


def test_the_latest_deployment_whose_latest_status_is_success_is_used(api: Api) -> None:
    m = _mod()
    api.deploy("qa", (9, "main", "new", "failure"), (8, "main", "old", f"{URL}/100"))
    api.run(100, None, RELEASE_PATH)
    api.artifact(100, 1000, _digests(D2))
    images, _, _ = m.collect(api, REPO)
    assert set(images) == {D2}


def test_an_ops_check_deployment_newer_than_the_release_one_is_ignored(api: Api) -> None:
    m = _mod()
    api.deploy("production", (9, "main", "mainhead", f"{URL}/200"), (8, "v1", "tag", f"{URL}/100"))
    api.run(200, None, OPS_PATH)
    api.jobs(200, None, "check (production)")
    api.run(100, None, RELEASE_PATH)
    api.artifact(100, 1000, _digests(D1))
    images, bad, _ = m.collect(api, REPO)
    assert bad == []
    assert images[D1]["where"] == ["production"]


def test_a_rollback_is_reported_and_the_digest_is_not_guessed(api: Api) -> None:
    m = _mod()
    api.deploy("production", (9, "main", "x", f"{URL}/200"), (8, "v1", "tag", f"{URL}/100"))
    api.run(200, None, OPS_PATH)
    api.jobs(200, None, "rollback (production)")
    images, bad, _ = m.collect(api, REPO)
    assert images == {}
    assert bad[0]["where"] == "production"
    assert ROLLBACK in bad[0]["reason"]
    assert "redeploy or inspect the cluster" in bad[0]["reason"]
    assert not any("/runs/100" in p for p in api.paths)


def test_a_rerun_uses_the_artifact_of_the_attempt_that_deployed(api: Api) -> None:
    m = _mod()
    api.deploy("dev", (7, "main", "aaa", f"{URL}/100/attempts/1"))
    api.run(100, 1, RELEASE_PATH, window=(T0, T1))
    api.artifact(100, 1001, _digests(D1), created=T0)  # the attempt that deployed
    api.artifact(100, 1002, _digests(D2), created=T3)  # a later re-run, other digests
    images, _, _ = m.collect(api, REPO)
    assert set(images) == {D1}


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/o/r/actions/runs/100",
        f"https://github.com/other/repo/actions/runs/100",  # noqa: F541
        f"{URL}/abc",
        f"{URL}/100/attempts/x",
        "",
    ],
)
def test_a_malformed_status_url_is_reported(api: Api, url: str) -> None:
    m = _mod()
    api.deploy("dev", (7, "main", "aaa", url))
    images, bad, _ = m.collect(api, REPO)
    assert images == {}
    assert bad[0]["where"] == "dev"
    assert "unrecognised deployment status URL" in bad[0]["reason"]


def test_a_run_of_another_workflow_is_not_trusted(api: Api) -> None:
    m = _mod()
    api.deploy("dev", (7, "main", "aaa", f"{URL}/100"))
    api.run(100, None, ".github/workflows/ci.yml")
    images, bad, notes = m.collect(api, REPO)
    assert images == {}
    assert bad == []
    assert "dev: no successful release deployment" in notes
    assert not any("/artifacts" in p for p in api.paths)


def test_a_release_tag_deployment_prefers_the_release_assets_over_any_artifact(api: Api) -> None:
    m = _mod()
    release = {
        "tag_name": "v1.0.0",
        "draft": False,
        "assets": [{"name": "image-digests.env", "id": 1}, {"name": "scan.env", "id": 2}],
    }
    api.routes[f"repos/{REPO}/releases/tags/v1.0.0"] = release
    d = _digests(D1)
    api.routes[f"repos/{REPO}/releases/assets/1"] = d["image-digests.env"].encode()
    api.routes[f"repos/{REPO}/releases/assets/2"] = d["scan.env"].encode()
    api.deploy("production", (5, "v1.0.0", "sss", f"{URL}/100"))
    api.run(100, None, RELEASE_PATH)
    images, bad, _ = m.collect(api, REPO)
    assert bad == []
    assert images[D1]["where"] == ["production"]
    assert not any("/artifacts" in p for p in api.paths)
    assert not any(p.endswith("releases?per_page=30") for p in api.paths)  # no list of releases


def test_the_latest_release_endpoint_is_used_and_falls_back_to_its_tag_run(api: Api) -> None:
    m = _mod()
    api.routes[f"repos/{REPO}/releases/latest"] = {
        "tag_name": "v1.0.0",
        "draft": False,
        "prerelease": False,
        "assets": [],
    }
    api.routes[f"repos/{REPO}/commits/v1.0.0"] = {"sha": "ttt"}
    api.routes[f"repos/{REPO}/actions/workflows/release.yml/runs?head_sha=ttt"] = {
        "workflow_runs": [
            {"id": 300, "head_branch": "main", "run_attempt": 1},
            {"id": 400, "head_branch": "v1.0.0", "run_attempt": 1},
        ]
    }
    api.run(400, 1, RELEASE_PATH)
    api.artifact(400, 4000, _digests(D1))
    images, _, _ = m.collect(api, REPO)
    assert images[D1]["where"] == ["release v1.0.0"]
    assert not any("/runs/300" in p for p in api.paths)


def test_an_expired_artifact_is_reported_not_skipped(api: Api) -> None:
    m = _mod()
    api.deploy("dev", (7, "main", "aaa", f"{URL}/100"))
    api.run(100, None, RELEASE_PATH)
    api.artifact(100, 1000, _digests(D1), expired=True)
    images, bad, _ = m.collect(api, REPO)
    assert images == {}
    assert bad == [{"where": "dev", "reason": EXPIRED}]


def test_a_release_run_without_an_artifact_is_reported(api: Api) -> None:
    m = _mod()
    api.deploy("dev", (7, "main", "aaa", f"{URL}/100"))
    api.run(100, None, RELEASE_PATH)
    api.routes[f"repos/{REPO}/actions/runs/100/artifacts?name=image-digests"] = {"artifacts": []}
    _, bad, _ = m.collect(api, REPO)
    assert bad[0]["where"] == "dev"
    assert "no image-digests artifact" in bad[0]["reason"]


def test_digests_are_deduplicated_across_environments_and_the_release(api: Api) -> None:
    m = _mod()
    api.deploy("dev", (7, "main", "aaa", f"{URL}/100"))
    api.deploy("qa", (8, "main", "aaa", f"{URL}/100"))
    api.run(100, None, RELEASE_PATH)
    api.artifact(100, 1000, _digests(D1))
    api.routes[f"repos/{REPO}/releases/latest"] = {
        "tag_name": "v1.0.0",
        "assets": [{"name": "image-digests.env", "id": 1}, {"name": "scan.env", "id": 2}],
    }
    d = _digests(D1)
    api.routes[f"repos/{REPO}/releases/assets/1"] = d["image-digests.env"].encode()
    api.routes[f"repos/{REPO}/releases/assets/2"] = d["scan.env"].encode()
    images, _, _ = m.collect(api, REPO)
    assert list(images) == [D1]
    assert images[D1]["where"] == ["dev", "qa", "release v1.0.0"]


def test_the_scan_input_carries_no_vex(api: Api, tmp_path: Path) -> None:
    m = _mod()
    api.deploy("dev", (7, "main", "aaa", f"{URL}/100"))
    api.run(100, None, RELEASE_PATH)
    api.artifact(100, 1000, _digests(D1))
    images, bad, _ = m.collect(api, REPO)
    m.write(tmp_path, images, bad)
    (img,) = json.loads((tmp_path / "digests.json").read_text())["images"]
    assert "vex" not in img
    assert not (tmp_path / "vex").exists()
    assert not any(p.endswith("name=vex") or "name=vex&" in p for p in api.paths)


@pytest.mark.parametrize(
    "ref",
    ["ghcr.io/evil/img/backend:1", "docker.io/o/r/backend:1", "ghcr.io/o/r/../x:1", "x;rm -rf /:1"],
)
def test_a_foreign_registry_or_path_is_refused(ref: str) -> None:
    m = _mod()
    with pytest.raises(ValueError, match="unusable"):
        m.build_from_env(f"BACKEND_IMAGE_DIGEST={D1}\n", f"BACKEND_IMAGE={ref}\n", REPO)


def test_an_unusable_digest_is_refused() -> None:
    m = _mod()
    with pytest.raises(ValueError, match="unusable"):
        m.build_from_env(
            "BACKEND_IMAGE_DIGEST=sha256:zz\n", "BACKEND_IMAGE=ghcr.io/o/r/b:1\n", REPO
        )


def test_a_failing_api_call_is_an_error_not_an_empty_result() -> None:
    m = _mod()

    def boom(args: list[str]) -> bytes:
        msg = f"gh {args[1]} failed"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError):
        m.collect(boom, REPO)


def test_main_writes_digests_json(
    api: Api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    m = _mod()
    api.deploy("dev", (7, "main", "aaa", f"{URL}/100"))
    api.run(100, None, RELEASE_PATH)
    api.artifact(100, 1000, _digests(D1))
    monkeypatch.setenv("GH_REPO", REPO)
    assert m.main(["x", str(tmp_path)], api) == 0
    (img,) = json.loads((tmp_path / "digests.json").read_text())["images"]
    assert img["short"] == "1" * 12
