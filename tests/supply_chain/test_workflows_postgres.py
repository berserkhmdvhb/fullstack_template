"""The backend workflow runs integration and e2e on a pinned PostgreSQL service container."""

from __future__ import annotations

import re
from typing import Any, cast

import yaml

from tests.supply_chain._workflows import REPO, workflow

IMAGE = re.compile(
    r"^postgres:(?P<tag>(?P<major>\d+)(?:\.\d+)*-(?P<variant>[a-z]+)\S*)@sha256:[0-9a-f]{64}$"
)
POSTGRES_JOBS = ("integration", "e2e")


def _jobs() -> dict[str, Any]:
    return cast("dict[str, Any]", workflow("reusable-backend.yml")["jobs"])


def _chart_image() -> str:
    values = yaml.safe_load((REPO / "charts/your-service-name/values.yaml").read_text("utf-8"))
    return str(values["postgresql"]["image"])


def test_integration_and_e2e_have_a_postgres_service_with_a_health_check() -> None:
    for name in POSTGRES_JOBS:
        service = _jobs()[name]["services"]["postgres"]
        assert "pg_isready" in service["options"], name
        assert "--health-retries" in service["options"], name
        assert "POSTGRES_PASSWORD" in service["env"], name


def test_unit_and_lint_stay_off_postgres() -> None:
    for name in ("unit", "lint"):
        assert "services" not in _jobs()[name]
        assert "TEST_DATABASE_URL" not in _jobs()[name].get("env", {})


def test_the_image_is_pinned_by_digest_and_identical_in_both_jobs() -> None:
    images = {_jobs()[n]["services"]["postgres"]["image"] for n in POSTGRES_JOBS}
    assert len(images) == 1, images
    assert IMAGE.match(images.pop())


def test_the_ci_image_matches_the_chart_major_and_variant() -> None:
    ci = IMAGE.match(_jobs()["integration"]["services"]["postgres"]["image"])
    chart = re.match(r"^postgres:(?P<major>\d+)(?:\.\d+)*-(?P<variant>[a-z]+)", _chart_image())
    assert ci, _chart_image()
    assert chart, _chart_image()
    assert (ci["major"], ci["variant"]) == (chart["major"], chart["variant"])


def test_tests_get_the_database_url_from_the_job_env() -> None:
    for name in POSTGRES_JOBS:
        url = _jobs()[name]["env"]["TEST_DATABASE_URL"]
        assert url.startswith("postgresql+asyncpg://"), name
        assert "secrets." not in url, "the password is a CI-only literal"


def test_the_contract_tests_run_in_the_integration_job() -> None:
    runs = "\n".join(str(s.get("run", "")) for s in _jobs()["integration"]["steps"])
    assert "make test-contract" in runs


def test_no_expression_in_a_run_script() -> None:
    for name, job in _jobs().items():
        for step in job["steps"]:
            assert "${{" not in str(step.get("run", "")), (name, step.get("name"))


def test_the_lint_job_runs_squawk_on_the_pinned_binary() -> None:
    runs = "\n".join(str(s.get("run", "")) for s in _jobs()["lint"]["steps"])
    assert runs.index("scripts/tools.sh install squawk") < runs.index("bash scripts/ci/squawk.sh")
    assert "continue-on-error" not in _jobs()["lint"]


def test_the_repo_job_installs_squawk_for_its_bats_test() -> None:
    runs = "\n".join(str(s.get("run", "")) for s in workflow("ci.yml")["jobs"]["repo"]["steps"])
    assert "install shellcheck bats yq kyverno squawk" in runs
    assert "bats tests/scripts/" in runs
