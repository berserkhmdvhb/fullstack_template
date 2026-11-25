"""scripts/ci/drift-render.sh: the deployed commit, rendered through its own deploy path."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.backend.test_chart_production_guard import find_helm, require_helm

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ci" / "drift-render.sh"
DIGEST = "sha256:" + "b" * 64
# What the deploy needs of the deployed commit: its deploy path, image manifest and chart.
COPY = (
    "scripts/ci/deploy-env.sh",
    "scripts/ci/migration-job-lib.sh",
    ".github/workflows/deploy.yml",
    ".github/scripts/container_matrix.py",
    ".github/container-images.yml",
    "VERSION",
    "charts/your-service-name",
)
# An adopter's dev values: what the placeholder guard asks for.
ADOPTED = {
    "edge": {"gateway": {"parentRefs": [{"name": "gw"}]}, "hosts": {"root": "app.example.org"}},
    "secrets": {"externalSecret": {"secretStoreRef": {"name": "store"}}},
}


def _merge(into: dict[str, Any], top: dict[str, Any]) -> dict[str, Any]:
    for k, v in top.items():
        into[k] = _merge(into[k], v) if isinstance(v, dict) and isinstance(into.get(k), dict) else v
    return into


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org"}
    env |= {"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org"}
    cmd = ["git", "-C", str(repo), *args]
    done = subprocess.run(cmd, capture_output=True, text=True, env=env, check=True)
    return done.stdout.strip()


def _deployed(tmp_path: Path, *, old: bool = False) -> tuple[Path, str]:
    repo = tmp_path / "deployed"
    for rel in COPY:
        src, dst = ROOT / rel, repo / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst)
        elif src.exists():
            shutil.copy2(src, dst)
    values = repo / "charts" / "your-service-name" / "values.dev.yaml"
    values.write_text(yaml.safe_dump(_merge(yaml.safe_load(values.read_text()), ADOPTED)))
    if old:  # a deploy script from before the render-only mode
        script = repo / "scripts" / "ci" / "deploy-env.sh"
        script.write_text('#!/usr/bin/env bash\necho deployed > "$WORK_DIR/ran"\n')
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "deployed")
    return repo, _git(repo, "rev-parse", "HEAD")


def _run(tmp_path: Path, repo: Path, sha: str, **extra: str) -> subprocess.CompletedProcess[str]:
    helm = require_helm(find_helm(), ci=bool(os.environ.get("CI")))
    env = {
        "PATH": f"{Path(helm).parent}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "DRIFT_ENV": "dev",
        "DEPLOYED_DIR": str(repo),
        "DEPLOYED_REF": "main",
        "DEPLOYED_SHA": sha,
        "DIGESTS": json.dumps({"BACKEND_IMAGE_DIGEST": DIGEST, "FRONTEND_IMAGE_DIGEST": DIGEST}),
        "SERVICE_NAME": "svc",
        "PYTHON": sys.executable,
        "OUT": str(tmp_path / "render.yaml"),
        "WORK_DIR": str(tmp_path),
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_REPOSITORY_OWNER": "o",
        **extra,
    }
    cmd = ["bash", str(SCRIPT)]
    return subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)


def test_the_render_is_the_deployed_commits_release_with_the_deployed_digests(
    tmp_path: Path,
) -> None:
    repo, sha = _deployed(tmp_path)
    done = _run(tmp_path, repo, sha)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "nothing deployed" in done.stdout
    assert (tmp_path / "output").read_text() == "rendered=true\n"
    docs = [d for d in yaml.safe_load_all((tmp_path / "render.yaml").read_text()) if d]
    assert {d["metadata"]["namespace"] for d in docs} == {"svc-dev", "svc-dev-db"}
    assert {d["metadata"]["labels"]["app.kubernetes.io/instance"] for d in docs} == {"svc"}
    images = {
        c["image"]
        for d in docs
        if d["kind"] == "Deployment"
        for c in d["spec"]["template"]["spec"]["containers"]
    }
    assert images == {f"ghcr.io/o/r/{i}@{DIGEST}" for i in ("backend", "frontend")}
    # The tag the release computed for main at that commit (dropped from the image by the digest).
    values = json.loads((tmp_path / "drift-dev" / "image-values.json").read_text())
    version = (ROOT / "VERSION").read_text().strip() if (ROOT / "VERSION").exists() else "0.0.0"
    assert values["images"]["backend"]["tag"] == f"{version}-main-{sha[:7]}"


def test_a_deployed_commit_without_the_render_mode_is_not_run(tmp_path: Path) -> None:
    repo, sha = _deployed(tmp_path, old=True)
    done = _run(tmp_path, repo, sha)
    assert done.returncode == 0, done.stderr
    assert "predates the drift render" in done.stdout
    assert (tmp_path / "output").read_text() == "rendered=false\n"
    assert not (tmp_path / "ran").exists()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"DEPLOYED_REF": "feature/x"}, "DEPLOYED_REF must be main or a v* tag"),
        ({"DEPLOYED_SHA": "f" * 40}, "is not a checkout of"),
        ({"DRIFT_ENV": "preview"}, "DRIFT_ENV must be dev, qa or production"),
        ({"DIGESTS": ""}, "DIGESTS is required"),
    ],
)
def test_bad_inputs_are_refused(tmp_path: Path, change: dict[str, str], message: str) -> None:
    repo, sha = _deployed(tmp_path)
    done = _run(tmp_path, repo, sha, **change)
    assert done.returncode == 1
    assert message in done.stderr
    assert not (tmp_path / "render.yaml").exists()
