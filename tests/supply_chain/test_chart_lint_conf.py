"""One tracked yamllint config for the chart, read by `ct lint` in CI and by the git hook."""

from __future__ import annotations

import yaml

from tests.supply_chain._pins import pin
from tests.supply_chain._workflows import REPO, workflow

CONF = ".github/ct-lintconf.yaml"
# The ct settings, one source for the chart job's ct lint and the kind job's ct lint and ct install.
CT_CONFIG = ".github/ct.yaml"


def _ct_config() -> dict[str, object]:
    config: dict[str, object] = yaml.safe_load((REPO / CT_CONFIG).read_text(encoding="utf-8"))
    return config


def test_the_config_is_tracked_and_says_where_it_came_from() -> None:
    first = (REPO / CONF).read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("# ")
    assert "chart-testing" in first
    ct = pin("ct")
    assert ct in first, "re-copy the config when the ct pin moves"


def test_the_chart_job_runs_ct_lint_with_it() -> None:
    steps = workflow("reusable-chart.yml")["jobs"]["chart"]["steps"]
    runs = "\n".join(s.get("run", "") for s in steps)
    assert f"ct lint --config {CT_CONFIG}" in runs
    assert _ct_config()["lint-conf"] == CONF
    assert "scripts/tools.sh install" in runs
    assert "ct" in runs


def test_the_hook_script_reads_the_same_file() -> None:
    script = (REPO / "scripts" / "ci" / "yamllint-values.sh").read_text(encoding="utf-8")
    assert CONF in script
    assert ".tools/opt" not in script


SCHEMA = ".github/ct-chart-schema.yaml"


def _chart_runs() -> str:
    steps = workflow("reusable-chart.yml")["jobs"]["chart"]["steps"]
    return "\n".join(s.get("run", "") for s in steps)


def test_the_chart_schema_is_tracked_and_matches_the_ct_pin() -> None:
    first = (REPO / SCHEMA).read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("# ")
    assert "chart_schema.yaml" in first
    ct = pin("ct")
    assert ct in first


def test_ct_lint_validates_the_chart_schema() -> None:
    config = _ct_config()
    assert config["chart-yaml-schema"] == SCHEMA
    assert config.get("validate-chart-schema", True) is True
    assert "--validate-chart-schema" not in _chart_runs()
    assert config["check-version-increment"] is False
    assert config["validate-maintainers"] is False


def test_the_kind_job_runs_ct_with_the_same_config() -> None:
    script = (REPO / "scripts" / "kind-deploy.sh").read_text(encoding="utf-8")
    assert f"ct lint --config {CT_CONFIG}" in script
    assert f"ct install --config {CT_CONFIG}" in script


def test_the_chart_job_installs_its_python_linters_from_hashed_locks() -> None:
    runs = _chart_runs()
    assert "for tool in yamllint yamale; do" in runs
    assert 'python3 -m venv "$RUNNER_TEMP/${tool}"' in runs
    assert "tools/requirements/${tool}.txt" in runs
    assert "--require-hashes" in runs
    assert 'PATH="$RUNNER_TEMP/yamllint/bin:$RUNNER_TEMP/yamale/bin:$PATH" ct lint --config' in runs
    assert "install ct" in runs
    assert "GITHUB_PATH" not in runs.replace('"$PWD/.tools/bin" >> "$GITHUB_PATH"', "")


def test_every_step_using_the_chart_dir_fails_closed_without_it() -> None:
    steps = workflow("reusable-chart.yml")["jobs"]["chart"]["steps"]
    users = [s for s in steps if "CHART_DIR" in s.get("run", "")]
    assert len(users) >= 2  # noqa: PLR2004
    for step in users:
        assert ': "${CHART_DIR:?' in step["run"], step.get("name")
