"""The kind node images (tools/kind-nodes.tsv): pinned by digest, the oldest one is the chart's
kubeVersion floor, and each leg names a pinned kubectl within one minor of its node."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from tests.supply_chain._pins import lock_entry, pin

ROOT = Path(__file__).resolve().parent.parent.parent
NODES = ROOT / "tools" / "kind-nodes.tsv"
CHART = ROOT / "charts" / "your-service-name" / "Chart.yaml"
IMAGE = re.compile(r"kindest/node:v(\d+)\.(\d+)\.\d+@sha256:[0-9a-f]{64}")


def legs() -> dict[str, tuple[str, str]]:
    rows = [
        ln.split("\t") for ln in NODES.read_text().splitlines() if ln and not ln.startswith("#")
    ]
    assert all(len(r) == 3 for r in rows), rows  # noqa: PLR2004 (leg, kubectl pin, image)
    return {leg: (kubectl, image) for leg, kubectl, image in rows}


def minor(version: str) -> tuple[int, int]:
    major, mnr = version.split(".")[:2]
    return int(major), int(mnr)


def test_there_are_two_legs_each_pinned_by_digest() -> None:
    nodes = legs()
    assert list(nodes) == ["floor", "newest"]
    for _kubectl, image in nodes.values():
        assert IMAGE.fullmatch(image), image


def test_the_floor_leg_is_the_charts_kube_version_floor_and_the_kubeconform_schemas() -> None:
    floor = re.fullmatch(r">=(\d+)\.(\d+)\.0-0", yaml.safe_load(CHART.read_text())["kubeVersion"])
    assert floor
    image = IMAGE.fullmatch(legs()["floor"][1])
    assert image
    assert (image[1], image[2]) == (floor[1], floor[2])
    assert minor(pin("k8s-openapi")) == (int(floor[1]), int(floor[2]))


def test_the_newest_leg_is_newer_than_the_floor() -> None:
    nodes = legs()
    floor, newest = (IMAGE.fullmatch(nodes[leg][1]) for leg in ("floor", "newest"))
    assert floor
    assert newest
    assert (int(newest[1]), int(newest[2])) > (int(floor[1]), int(floor[2]))


def test_each_leg_names_a_pinned_kubectl_within_one_minor_of_its_node() -> None:
    for leg, (kubectl, image) in legs().items():
        version = pin(kubectl)
        locked = lock_entry(kubectl)
        assert locked["url"].startswith(f"https://dl.k8s.io/v{version}/bin/"), locked
        node = IMAGE.fullmatch(image)
        assert node
        client = minor(version)
        assert client[0] == int(node[1]), leg
        assert abs(client[1] - int(node[2])) <= 1, (leg, version, image)
