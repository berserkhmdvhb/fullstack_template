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


def _env(digest: str, name: str = "BACKEND") -> tuple[str, str]:
    tag = "1.0.0-rel-abc"
    return f"{name}_IMAGE_DIGEST={digest}\n", f"{name}_IMAGE=ghcr.io/o/r/backend:{tag}\n"


class Api:
    """A fake `gh`: routes `gh api <path>` to a table of JSON or bytes, and records the paths."""

    def __init__(self) -> None:
        self.routes: dict[str, object] = {}
        self.paths: list[str] = []

    def __call__(self, args: list[str]) -> bytes:
        assert args[0] == "api", args
        path = args[-1]
        self.paths.append(path)
        for prefix, value in self.routes.items():
            if path.startswith(prefix):
                return value if isinstance(value, bytes) else json.dumps(value).encode()
        msg = f"unrouted: {path}"
        raise AssertionError(msg)

    def deploy(self, env: str, *deps: tuple[int, str, str, str]) -> None:
        """deps: (id, ref, sha, latest status state), newest first."""
        self.routes[f"repos/{REPO}/deployments?environment={env}"] = [
            {"id": i, "ref": ref, "sha": sha} for i, ref, sha, _ in deps
        ]
        for i, _, _, state in deps:
            self.routes[f"repos/{REPO}/deployments/{i}/statuses"] = [{"state": state}]

    def run(self, sha: str, *runs: tuple[int, str]) -> None:
        self.routes[f"repos/{REPO}/actions/workflows/release.yml/runs?head_sha={sha}"] = {
            "workflow_runs": [{"id": i, "head_branch": b} for i, b in runs]
        }

    def artifact(
        self, run_id: int, name: str, files: dict[str, str] | None, *, expired: bool = False
    ) -> None:
        base = f"repos/{REPO}/actions/runs/{run_id}/artifacts?name={name}"
        aid = run_id * 10 + (1 if name == "vex" else 0)
        self.routes[base] = {
            "artifacts": [] if files is None else [{"id": aid, "expired": expired}]
        }
        if files is not None:
            self.routes[f"repos/{REPO}/actions/artifacts/{aid}/zip"] = _zip(files)


def _digests(digest: str) -> dict[str, str]:
    d, r = _env(digest)
    return {"image-digests.env": d, "scan.env": r}


@pytest.fixture
def api() -> Api:
    a = Api()
    a.routes[f"repos/{REPO}/releases?per_page"] = []
    for env in ("dev", "qa", "production"):
        a.routes[f"repos/{REPO}/deployments?environment={env}"] = []
    return a


def test_a_deployment_is_mapped_through_its_sha_to_the_run_artifact(api: Api) -> None:
    m = _mod()
    api.deploy("dev", (7, "main", "aaa", "success"))
    api.run("aaa", (100, "main"))
    api.artifact(100, "image-digests", _digests(D1))
    api.artifact(100, "vex", {"openvex.json": '{"statements": []}'})
    images, bad, notes = m.collect(api, REPO)
    assert bad == []
    assert images[D1]["where"] == ["dev"]
    assert images[D1]["repository"] == "ghcr.io/o/r/backend"
    assert images[D1]["id"] == "backend"
    assert images[D1]["vex"] == b'{"statements": []}'
    assert "qa: no successful deployment" in notes


def test_the_latest_deployment_whose_latest_status_is_success_is_used(api: Api) -> None:
    m = _mod()
    api.deploy("qa", (9, "main", "new", "failure"), (8, "main", "old", "success"))
    api.run("old", (100, "main"))
    api.artifact(100, "image-digests", _digests(D2))
    api.artifact(100, "vex", None)
    images, _, notes = m.collect(api, REPO)
    assert set(images) == {D2}
    assert not any("new" in p for p in api.paths if "runs?head_sha" in p)
    assert any("no OpenVEX" in n for n in notes)


def test_the_run_for_the_deployment_ref_wins_over_another_run_of_the_same_sha(api: Api) -> None:
    m = _mod()
    api.deploy("production", (5, "v1.0.0", "sss", "success"))
    api.run("sss", (200, "main"), (300, "v1.0.0"))
    api.artifact(300, "image-digests", _digests(D3))
    api.artifact(300, "vex", None)
    images, _, _ = m.collect(api, REPO)
    assert set(images) == {D3}


def test_a_release_tag_deployment_prefers_the_release_assets_over_any_artifact(api: Api) -> None:
    m = _mod()
    d, r = _env(D1)
    release = {
        "tag_name": "v1.0.0",
        "draft": False,
        "assets": [
            {"name": "image-digests.env", "id": 1},
            {"name": "scan.env", "id": 2},
            {"name": "openvex.json", "id": 3},
        ],
    }
    api.routes[f"repos/{REPO}/releases?per_page"] = [release]
    api.routes[f"repos/{REPO}/releases/assets/1"] = d.encode()
    api.routes[f"repos/{REPO}/releases/assets/2"] = r.encode()
    api.routes[f"repos/{REPO}/releases/assets/3"] = b"{}"
    api.deploy("production", (5, "v1.0.0", "sss", "success"))
    images, bad, _ = m.collect(api, REPO)
    assert bad == []
    assert images[D1]["where"] == ["production", "release v1.0.0"]  # one digest, two places
    assert not any("/actions/" in p for p in api.paths)


def test_an_expired_artifact_is_reported_not_skipped(api: Api) -> None:
    m = _mod()
    api.deploy("dev", (7, "main", "aaa", "success"))
    api.run("aaa", (100, "main"))
    api.artifact(100, "image-digests", _digests(D1), expired=True)
    images, bad, _ = m.collect(api, REPO)
    assert images == {}
    assert bad == [{"where": "dev", "reason": EXPIRED}]


def test_a_commit_without_a_release_run_is_reported(api: Api) -> None:
    m = _mod()
    api.deploy("dev", (7, "main", "aaa", "success"))
    api.run("aaa")
    _, bad, _ = m.collect(api, REPO)
    assert bad[0]["where"] == "dev"
    assert "no release workflow run" in bad[0]["reason"]


def test_the_latest_published_release_falls_back_to_its_run_artifact(api: Api) -> None:
    m = _mod()
    api.routes[f"repos/{REPO}/releases?per_page"] = [
        {"tag_name": "v2.0.0", "draft": True, "assets": []},
        {"tag_name": "v1.0.0", "draft": False, "assets": []},
    ]
    api.routes[f"repos/{REPO}/commits/v1.0.0"] = {"sha": "ttt"}
    api.run("ttt", (400, "v1.0.0"))
    api.artifact(400, "image-digests", _digests(D1))
    api.artifact(400, "vex", None)
    images, _, _ = m.collect(api, REPO)
    assert images[D1]["where"] == ["release v1.0.0"]


def test_an_unusable_digest_is_refused_not_passed_to_the_scanner() -> None:
    m = _mod()
    with pytest.raises(ValueError, match="unusable"):
        m.build_from_env(
            "BACKEND_IMAGE_DIGEST=sha256:zz\n", "BACKEND_IMAGE=ghcr.io/o/r/b:1\n", None
        )
    with pytest.raises(ValueError, match="unusable"):
        m.build_from_env(f"BACKEND_IMAGE_DIGEST={D1}\n", "BACKEND_IMAGE=x;rm -rf /:1\n", None)


def test_a_failing_api_call_is_an_error_not_an_empty_result() -> None:
    m = _mod()

    def boom(args: list[str]) -> bytes:
        msg = f"gh {args[1]} failed"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError):
        m.collect(boom, REPO)


def test_main_writes_digests_json_and_the_vex_files(
    api: Api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    m = _mod()
    api.deploy("dev", (7, "main", "aaa", "success"))
    api.run("aaa", (100, "main"))
    api.artifact(100, "image-digests", _digests(D1))
    api.artifact(100, "vex", {"openvex.json": "{}"})
    monkeypatch.setenv("GH_REPO", REPO)
    assert m.main(["x", str(tmp_path)], api) == 0
    doc = json.loads((tmp_path / "digests.json").read_text())
    (img,) = doc["images"]
    assert img["short"] == "1" * 12
    assert (tmp_path / img["vex"]).read_text() == "{}"
