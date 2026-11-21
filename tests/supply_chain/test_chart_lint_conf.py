"""One tracked yamllint config for the chart, read by `ct lint` in CI and by the pre-commit hook."""

from __future__ import annotations

from tests.supply_chain._workflows import REPO, workflow

CONF = ".github/ct-lintconf.yaml"


def test_the_config_is_tracked_and_says_where_it_came_from() -> None:
    first = (REPO / CONF).read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("# ")
    assert "chart-testing" in first
    pin = next(
        ln.split("\t")[1]
        for ln in (REPO / "tools" / "pins.tsv").read_text(encoding="utf-8").splitlines()
        if ln.startswith("ct\t")
    )
    assert pin in first, "re-copy the config when the ct pin moves"


def test_the_chart_job_runs_ct_lint_with_it() -> None:
    steps = workflow("reusable-chart.yml")["jobs"]["chart"]["steps"]
    runs = "\n".join(s.get("run", "") for s in steps)
    assert f"--lint-conf {CONF}" in runs
    assert "scripts/tools.sh install" in runs
    assert "ct" in runs


def test_the_hook_script_reads_the_same_file() -> None:
    script = (REPO / "scripts" / "ci" / "yamllint-values.sh").read_text(encoding="utf-8")
    assert CONF in script
    assert ".tools/opt" not in script
