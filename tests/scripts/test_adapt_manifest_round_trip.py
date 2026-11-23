"""The adapter renames; it never drops a key of the image manifest.

Run over a temp copy holding the real manifest, the scan config and the root Dockerfiles, the
adapter must return the manifest unchanged except for the renamed package name: every image keeps
`distroless`, `platforms`, `healthcheck_exempt` and the rest, and `runners` survives.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
MANIFEST = Path(".github/container-images.yml")
SCAN = Path("infra/security/config.yaml")


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
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


def _with_manifest(root: Path, text: str) -> None:
    _adapt(root)  # lay out the tree
    (root / MANIFEST).write_text(text, encoding="utf-8")


def test_a_flow_style_entry_is_refused_clearly_and_nothing_is_written(tmp_path: Path) -> None:
    text = (
        (REPO / MANIFEST)
        .read_text(encoding="utf-8")
        .replace(
            "  - id: waf\n",
            "  - {id: extra, repository: r, dockerfile: Dockerfile.extra}\n  - id: waf\n",
        )
    )
    _with_manifest(tmp_path, text)
    proc = _run(tmp_path)
    assert proc.returncode != 0
    assert "flow-style" in proc.stderr
    assert (tmp_path / MANIFEST).read_text(encoding="utf-8") == text


def test_a_blank_line_inside_the_images_block_is_refused_clearly(tmp_path: Path) -> None:
    text = (REPO / MANIFEST).read_text(encoding="utf-8").replace("  - id: waf\n", "\n  - id: waf\n")
    _with_manifest(tmp_path, text)
    proc = _run(tmp_path)
    assert proc.returncode != 0
    assert "blank line" in proc.stderr
    assert (tmp_path / MANIFEST).read_text(encoding="utf-8") == text


# Spelt in capitals so the adapter, which rewrites this file, leaves the placeholder alone.
_OLD_METRIC = "YOUR_SERVICE_NAME_BUILD_INFO".lower()
# The tests that read wiki/ (which the adapter leaves generic) against the code and the chart.
_DOC_TESTS = (
    "tests/backend/test_chart_dashboards.py",
    "tests/backend/test_chart_monitoring.py",
)


def test_an_adapted_copy_passes_the_tests_that_read_the_docs_against_the_code(
    tmp_path: Path,
) -> None:
    """The info metric is renamed in the dashboards and the collector, a valid metric name, and the
    doc-reading tests (the runbook stays generic) still pass in the adapted copy."""
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],  # noqa: S607
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    for rel in filter(None, listed.split("\0")):
        if (REPO / rel).is_file():
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO / rel, tmp_path / rel)
    (tmp_path / ".tools").symlink_to(REPO / ".tools")  # the pinned helm and promtool
    proc = _run(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    collector = (tmp_path / "src/acme/backend/build_info.py").read_text(encoding="utf-8")
    found = re.search(r'BUILD_INFO_METRIC: Final = "([^"]+)"', collector)
    assert found, collector
    metric = found.group(1)
    assert metric == "acme_build_info"
    assert re.fullmatch(r"[a-zA-Z_:][a-zA-Z0-9_:]*", metric)
    for dashboard in (tmp_path / "charts/acme/dashboards").glob("*.json"):
        text = dashboard.read_text(encoding="utf-8")
        assert metric in text, dashboard
        assert _OLD_METRIC not in text, dashboard

    tests = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", *_DOC_TESTS],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(tmp_path / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert tests.returncode == 0, tests.stdout[-3000:] + tests.stderr[-1000:]
