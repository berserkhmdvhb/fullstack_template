"""Dockerfile.frontend pins node by digest, and the builder's node is the pinned tooling node."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DOCKERFILE = (ROOT / "Dockerfile.frontend").read_text(encoding="utf-8")
FROM = re.compile(r"^FROM (\S+) AS (\w+)$", re.MULTILINE)


def _stage(name: str) -> str:
    return next(m[1] for m in FROM.finditer(DOCKERFILE) if m[2] == name)


def _pinned_node_version() -> str:
    rows = (ROOT / "tools/pins.tsv").read_text(encoding="utf-8").splitlines()
    return next(r.split("\t")[1] for r in rows if r.startswith("node\t"))


def test_the_builder_node_is_the_pinned_node_version() -> None:
    match = re.fullmatch(r"node:([\d.]+)-bookworm-slim@sha256:[0-9a-f]{64}", _stage("builder"))
    assert match, "the builder must be node:<version>-bookworm-slim pinned by digest"
    assert match.group(1) == _pinned_node_version()


def test_the_runtime_is_the_engines_nodejs_major_pinned_by_digest() -> None:
    engines = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))["engines"]
    major = re.match(r">=(\d+)", engines["node"]).group(1)  # type: ignore[union-attr]
    assert re.fullmatch(
        rf"gcr\.io/distroless/nodejs{major}-debian\d+@sha256:[0-9a-f]{{64}}", _stage("runtime")
    )
