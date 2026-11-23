"""The kind harness exports a node's image for the node's own architecture (tests/k8s/_kube.py),
so an arm64 host pushes its arm64 images. Offline."""

from __future__ import annotations

import pytest

from tests.k8s._kube import ROOT, oci_platform


@pytest.mark.parametrize(
    ("machine", "platform"),
    [("x86_64\n", "linux/amd64"), ("aarch64\n", "linux/arm64"), ("arm64", "linux/arm64")],
)
def test_a_node_machine_maps_to_its_oci_platform(machine: str, platform: str) -> None:
    assert oci_platform(machine) == platform


def test_an_unknown_machine_is_refused() -> None:
    with pytest.raises(AssertionError, match="riscv64"):
        oci_platform("riscv64")


def test_the_harness_names_no_architecture_literal() -> None:
    source = (ROOT / "tests" / "k8s" / "_kube.py").read_text(encoding="utf-8")
    assert "--platform linux/" not in source
