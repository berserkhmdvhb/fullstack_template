"""Dockerfile.frontend pins node by digest, and the builder's node is the pinned tooling node."""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
DOCKERFILE = (ROOT / "Dockerfile.frontend").read_text(encoding="utf-8")
FROM = re.compile(r"^FROM (\S+) AS (\w+)$", re.MULTILINE)


def _stage(name: str) -> str:
    return next(m[1] for m in FROM.finditer(DOCKERFILE) if m[2] == name)


def _pinned_node_version() -> str:
    rows = (ROOT / "tools/pins.tsv").read_text(encoding="utf-8").splitlines()
    return next(r.split("\t")[1] for r in rows if r.startswith("node\t"))


def test_the_builder_node_is_the_pinned_node_version() -> None:
    match = re.fullmatch(r"node:([\d.]+)-trixie-slim@sha256:[0-9a-f]{64}", _stage("builder"))
    assert match, "the builder must be node:<version>-trixie-slim pinned by digest"
    assert match.group(1) == _pinned_node_version()


def test_the_nginx_frontend_builder_is_the_pinned_node_version() -> None:
    nginx = (ROOT / "Dockerfile.nginx").read_text(encoding="utf-8")
    match = re.search(r"^FROM (\S+) AS frontend-builder$", nginx, re.MULTILINE)
    assert match
    assert match[1].startswith(f"node:{_pinned_node_version()}-trixie-slim@sha256:")


def test_the_version_files_and_engines_follow_the_pin() -> None:
    pin = _pinned_node_version()
    for name in (".nvmrc", ".node-version"):
        assert (ROOT / "frontend" / name).read_text(encoding="utf-8").strip() == pin
    engines = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))["engines"]
    major = int(pin.split(".")[0])
    assert engines["node"] == f">={major} <{major + 1}"


def test_package_manager_is_the_npm_bundled_with_the_pinned_node() -> None:
    pin = _pinned_node_version()
    meta = ROOT / f".tools/opt/node-{pin}/node-v{pin}-linux-x64/lib/node_modules/npm/package.json"
    if not meta.is_file():
        pytest.skip("the pinned Node is not installed in .tools (make tools)")
    bundled = json.loads(meta.read_text(encoding="utf-8"))["version"]
    package = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    assert package["packageManager"] == f"npm@{bundled}"


def test_the_runtime_is_the_pinned_nodejs_major_pinned_by_digest() -> None:
    major = _pinned_node_version().split(".")[0]
    assert re.fullmatch(
        rf"gcr\.io/distroless/nodejs{major}-debian\d+@sha256:[0-9a-f]{{64}}", _stage("runtime")
    )
