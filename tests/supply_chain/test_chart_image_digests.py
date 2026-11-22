"""Every third-party image the chart references is pinned by digest, and Renovate sees each pin.

The repository's own images (images.backend, images.frontend, images.waf) get their digests from the
release at deploy time (scripts/ci/deploy-env.sh), so they are the only ones without one here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
CHART = ROOT / "charts" / "your-service-name"
VALUES_FILES = [CHART / "values.yaml", *sorted(CHART.glob("values.*.yaml"))]
RELEASE_IMAGES = {"images.backend", "images.frontend", "images.waf"}
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
INLINE = re.compile(r"^[a-z0-9][\w./-]*:[\w][\w.-]*@sha256:[0-9a-f]{64}$")


def _images(node: Any, path: str = "") -> Iterator[tuple[str, Any]]:  # noqa: ANN401
    """(path, value) of each image: a mapping with a repository, or a string named image.

    Lists are walked too (an init container's image), so nothing hides in one.
    """
    if isinstance(node, list):
        for index, item in enumerate(node):
            yield from _images(item, f"{path}[{index}]")
    elif isinstance(node, dict):
        if "repository" in node:
            yield path, node
            return
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            if isinstance(value, str) and (key == "image" or str(key).endswith("Image")):
                yield child, value
            else:
                yield from _images(value, child)


def _all() -> list[tuple[str, str, Any]]:
    return [
        (f.name, path, value)
        for f in VALUES_FILES
        for path, value in _images(yaml.safe_load(f.read_text(encoding="utf-8")))
    ]


def test_the_walk_finds_the_third_party_images() -> None:
    found = {path for name, path, _ in _all() if name == "values.yaml"}
    assert {
        "postgresql.image",
        "postgresql.clientImage",
        "cloudSqlProxy.image",
        *RELEASE_IMAGES,
    } <= found


@pytest.mark.parametrize(("name", "path", "value"), _all(), ids=lambda v: str(v)[:40])
def test_every_third_party_image_has_a_sha256_digest(name: str, path: str, value: Any) -> None:  # noqa: ANN401
    if path in RELEASE_IMAGES:
        return
    if isinstance(value, dict):
        assert DIGEST.fullmatch(str(value.get("digest", ""))), f"{name}: {path} has no digest"
    else:
        assert INLINE.fullmatch(value), f"{name}: {path} must be repository:tag@sha256:<64 hex>"


def _renovate_patterns() -> list[re.Pattern[str]]:
    text = (ROOT / "renovate.json5").read_text(encoding="utf-8")
    config = json.loads(re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE))
    return [
        re.compile(re.sub(r"\(\?<([A-Za-z]+)>", r"(?P<\1>", ms), re.MULTILINE)
        for m in config["customManagers"]
        if any(re.search(p.strip("/"), "charts/x/values.yaml") for p in m["managerFilePatterns"])
        for ms in m["matchStrings"]
    ]


def test_renovate_sees_every_pinned_image_in_values_yaml() -> None:
    text = (CHART / "values.yaml").read_text(encoding="utf-8")
    seen = {
        (m.group("depName"), m.group("currentDigest"))
        for pattern in _renovate_patterns()
        for m in pattern.finditer(text)
    }
    pinned = {
        (path, value["digest"] if isinstance(value, dict) else value.rsplit("@", 1)[1])
        for name, path, value in _all()
        if name == "values.yaml" and path not in RELEASE_IMAGES
    }
    assert pinned
    assert {digest for _, digest in pinned} <= {digest for _, digest in seen}
    assert {"postgres", "gcr.io/cloud-sql-connectors/cloud-sql-proxy"} <= {d for d, _ in seen}


def test_the_walk_finds_tag_less_mappings_and_images_inside_lists() -> None:
    """A mutation must go red: `image: {repository: nginx}` (no tag, no digest), and an image
    string inside a list (an extra init container), are images too."""
    doc = {
        "extra": {"image": {"repository": "nginx"}},
        "backend": {"extraInitContainers": [{"name": "x", "image": "busybox:1.36"}]},
    }
    found = dict(_images(doc))
    assert found == {
        "extra.image": {"repository": "nginx"},
        "backend.extraInitContainers[0].image": "busybox:1.36",
    }
