"""The adapter renames; it never drops a key of the image manifest.

Run over a temp copy holding the real manifest, the scan config and the root Dockerfiles, the
adapter must return the manifest unchanged except for the renamed package name: every image keeps
`distroless`, `platforms`, `healthcheck_exempt` and the rest, and `runners` survives.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
MANIFEST = Path(".github/container-images.yml")
SCAN = Path("infra/security/config.yaml")


def _adapt(root: Path) -> str:
    for rel in (MANIFEST, SCAN):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, root / rel)
    for dockerfile in REPO.glob("Dockerfile*"):
        shutil.copy(dockerfile, root / dockerfile.name)
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO / "infra/adapt/replace_service_name.py"),
            "--root",
            str(root),
            "--old",
            "your_service_name",
            "--new",
            "acme",
            "--backup-dir",
            str(root / ".adapt-backup"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return (root / MANIFEST).read_text(encoding="utf-8")


def test_the_adapter_only_renames_the_manifest(tmp_path: Path) -> None:
    before = (REPO / MANIFEST).read_text(encoding="utf-8")
    after = _adapt(tmp_path)
    expected: dict[str, Any] = yaml.safe_load(before)
    expected["version"]["package_name"] = "acme"
    assert yaml.safe_load(after) == expected
    assert after == before.replace("package_name: your_service_name", "package_name: acme"), (
        "comments and formatting survive too"
    )


def test_every_image_key_survives(tmp_path: Path) -> None:
    images = {i["id"]: i for i in yaml.safe_load(_adapt(tmp_path))["images"]}
    assert images["waf"]["healthcheck_exempt"]
    assert all(i["distroless"] is True for i in images.values())
    assert all(i["platforms"] == ["linux/amd64", "linux/arm64"] for i in images.values())


def test_a_new_dockerfile_gets_a_canonical_entry_and_the_others_keep_theirs(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile.worker").write_text("FROM scratch\n", encoding="utf-8")
    images = {i["id"]: i for i in yaml.safe_load(_adapt(tmp_path))["images"]}
    assert set(images) == {"backend", "frontend", "waf", "worker"}
    assert images["worker"]["dockerfile"] == "Dockerfile.worker"
    assert images["waf"]["healthcheck_exempt"]
