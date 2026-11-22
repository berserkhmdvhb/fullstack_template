# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The helm test hooks (templates/tests) render only with tests.enabled: no dev, qa or production
release carries them, local does, and with every option on they meet the restricted profile."""

from __future__ import annotations

import os
from typing import Any

import pytest

from tests.backend.test_chart_pod_security import ALL_ON, pod_specs, render, violations
from tests.backend.test_chart_production_guard import find_helm, require_helm

TEST_JOB = "Job/svc-your-service-name-test"


@pytest.fixture(scope="module")
def helm() -> str:
    return require_helm(find_helm(), ci=bool(os.environ.get("CI")))


def hook_objects(docs: list[dict[str, Any]]) -> list[str]:
    return [
        f"{d['kind']}/{d['metadata']['name']}"
        for d in docs
        if (d["metadata"].get("annotations") or {}).get("helm.sh/hook") == "test"
    ]


@pytest.mark.parametrize(
    "values", ["values.yaml", "values.dev.yaml", "values.qa.yaml", "values.production.yaml"]
)
def test_no_release_but_local_carries_the_test_objects(helm: str, values: str) -> None:
    assert hook_objects(render(helm, values)) == []


def test_the_local_values_turn_the_tests_on(helm: str) -> None:
    assert hook_objects(render(helm, "values.local.yaml")) == [
        "NetworkPolicy/svc-your-service-name-test-egress",
        "NetworkPolicy/svc-your-service-name-test-ingress",
        TEST_JOB,
    ]


def test_with_every_option_on_the_test_jobs_meet_the_restricted_profile(helm: str) -> None:
    specs = pod_specs(render(helm, "values.yaml", *ALL_ON))
    assert TEST_JOB in specs, "ALL_ON must turn tests.enabled on"
    assert violations(*specs[TEST_JOB]) == []
