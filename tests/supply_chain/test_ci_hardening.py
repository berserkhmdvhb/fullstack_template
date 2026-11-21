"""Tests for the CI-hardening lint of the GitHub workflows (static, offline).

infra/security/ci_hardening_lint.py is the program, infra/security/check-ci-hardening.sh the frozen
entry point. Every check has pass* and fail* fixture dirs under fixtures/ci_hardening/<check>/.
A fail fixture must report exactly its own check.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.supply_chain._workflows import REPO, unpinned_uses

LINTER = REPO / "infra/security/ci_hardening_lint.py"
WRAPPER = REPO / "infra/security/check-ci-hardening.sh"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ci_hardening"
BASH: str = shutil.which("bash") or "/usr/bin/bash"

CHECKS = (
    "pinned-uses",
    "top-permissions",
    "no-prt-checkout",
    "no-inline-secrets",
    "image-digest",
    "image-push-isolation",
    "provenance",
    "osv-coverage",
    "environment-for-secrets",
    "timeouts",
    "no-dry-run-env",
    "documented-permissions",
)
USAGE_ERROR = 2
FINDING = re.compile(r"^(?P<file>\S+?):(?P<line>\d+): (?P<check>[a-z-]+) ", re.MULTILINE)


def run_lint(root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LINTER), *extra, str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def staged(fixture: Path, tmp_path: Path, *, with_security: bool = True) -> Path:
    """Copy a fixture and add a clean security.yml, so a fixture is judged on its own check only."""
    dest = tmp_path / "repo"
    shutil.copytree(fixture, dest)
    security = dest / ".github/workflows/security.yml"
    if with_security and not security.exists() and not security.with_suffix(".yaml").exists():
        security.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / "all-good/pass/.github/workflows/security.yml", security)
    return dest


def found(root: Path) -> set[str]:
    return {m["check"] for m in FINDING.finditer(run_lint(root).stdout)}


def variants(prefix: str) -> list[tuple[str, str]]:
    names = [*CHECKS, "composite"]
    return [
        (c, d.name)
        for c in names
        for d in sorted((FIXTURES / c).iterdir())
        if d.name.startswith(prefix)
    ]


ISOLATION = "image-push-isolation"

# What each fail fixture must report, and nothing else; the default is the directory's check.
EXPECTED: dict[tuple[str, str], set[str]] = {
    ("composite", "fail"): {"pinned-uses"},
    ("composite", "fail-yaml-suffix"): {"no-inline-secrets"},
    # write-all also grants packages, id-token and attestations, so the isolation rule fires too.
    ("environment-for-secrets", "fail-write-all"): {"environment-for-secrets", ISOLATION},
    ("environment-for-secrets", "fail-top-write-all"): {"environment-for-secrets", ISOLATION},
    ("documented-permissions", "fail-write-all"): {"documented-permissions", ISOLATION},
}


@pytest.mark.parametrize(("check", "variant"), variants("pass"))
def test_pass_fixture_is_clean(check: str, variant: str, tmp_path: Path) -> None:
    root = staged(FIXTURES / check / variant, tmp_path, with_security=check != "osv-coverage")
    result = run_lint(root)
    if check == "osv-coverage":
        assert "osv-coverage" not in found(root)
    else:
        assert result.returncode == 0, result.stdout
        assert found(root) == set()


@pytest.mark.parametrize(("check", "variant"), variants("fail"))
def test_fail_fixture_reports_exactly_its_check(check: str, variant: str, tmp_path: Path) -> None:
    expected = EXPECTED.get((check, variant), {check})
    root = staged(FIXTURES / check / variant, tmp_path, with_security=check != "osv-coverage")
    result = run_lint(root)
    assert found(root) == expected, result.stdout
    assert result.returncode == 1


def test_malformed_yaml_is_a_parse_error_not_a_traceback() -> None:
    result = run_lint(FIXTURES / "parse-error" / "fail")
    assert result.returncode == USAGE_ERROR
    assert re.search(r"^\.github/workflows/bad\.yml:\d+: parse-error ", result.stdout, re.MULTILINE)
    assert "Traceback" not in result.stderr
    assert (
        json.loads(run_lint(FIXTURES / "parse-error" / "fail", "--json").stdout)["status"]
        == "error"
    )


def test_clean_fixture_exits_zero_and_prints_a_summary() -> None:
    result = run_lint(FIXTURES / "all-good" / "pass")
    assert result.returncode == 0, result.stdout
    assert "ci-hardening: 0 finding(s)" in result.stdout


def test_findings_print_as_file_line_id_message() -> None:
    out = run_lint(FIXTURES / "timeouts" / "fail").stdout
    assert ".github/workflows/ci.yml:" in out
    assert re.search(
        r"^\.github/workflows/ci\.yml:\d+: timeouts job j has no timeout-minutes$",
        out,
        re.MULTILINE,
    )


def test_provenance_is_not_a_skip_any_more() -> None:
    result = run_lint(FIXTURES / "provenance" / "pass")
    assert "SKIP" not in result.stdout
    assert "PROVENANCE_ENABLED" not in LINTER.read_text()


def test_provenance_findings_name_what_is_missing() -> None:
    out = run_lint(FIXTURES / "provenance" / "fail-no-sbom-attest").stdout
    assert "actions/attest-sbom" in out
    assert (
        "reusable-build.yml" in run_lint(FIXTURES / "provenance" / "fail-missing-reusable").stdout
    )


def test_docker_reference_needs_a_digest_and_a_local_reference_is_exempt() -> None:
    out = run_lint(FIXTURES / "pinned-uses" / "fail").stdout
    assert "docker reference docker://alpine:3.20" in out
    assert "./.github/actions/local" not in out
    assert "actions/checkout@v4 is not pinned" in out
    assert "no '# vX.Y.Z' version comment" in out


def test_pull_request_target_is_banned_outright() -> None:
    assert "pull_request_target is banned" in run_lint(FIXTURES / "no-prt-checkout" / "fail").stdout


def test_json_report_is_valid_and_matches_the_exit_code() -> None:
    bad = run_lint(FIXTURES / "timeouts" / "fail", "--json")
    assert bad.returncode == 1
    doc = json.loads(bad.stdout)
    assert doc["status"] == "red"
    assert "skipped" not in doc
    assert (
        json.loads(run_lint(FIXTURES / "all-good" / "pass", "--json").stdout)["status"] == "green"
    )


def test_a_repo_without_workflows_is_a_usage_error(tmp_path: Path) -> None:
    assert run_lint(tmp_path).returncode == USAGE_ERROR


def test_the_real_repo_lints_clean() -> None:
    result = run_lint(REPO)
    assert result.returncode == 0, result.stdout


def test_wrapper_runs_the_lint_and_propagates_a_red_verdict(tmp_path: Path) -> None:
    env = {
        "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
        "SEC_OUTPUT_DIR": str(tmp_path / "out"),
    }
    good = subprocess.run(
        [BASH, str(WRAPPER), str(FIXTURES / "all-good" / "pass")],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert good.returncode == 0, good.stdout + good.stderr
    assert json.loads((tmp_path / "out/ci-hardening/report.json").read_text())["status"] == "green"
    bad = subprocess.run(
        [BASH, str(WRAPPER), str(FIXTURES / "timeouts" / "fail")],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert bad.returncode == 1
    assert json.loads((tmp_path / "out/ci-hardening/report.json").read_text())["status"] == "red"


def test_unpinned_uses_helper_treats_docker_and_local_refs_explicitly(tmp_path: Path) -> None:
    f = tmp_path / "w.yml"
    digest = "a" * 64
    f.write_text(
        "steps:\n"
        "  - uses: ./.github/actions/x\n"
        f"  - uses: docker://alpine@sha256:{digest}\n"
        "  - uses: docker://alpine:3.20\n"
        "  - uses: o/r@" + "b" * 40 + " # v1.2.3\n"
    )
    assert unpinned_uses(f) == ["- uses: docker://alpine:3.20"]


def test_ci_has_a_blocking_always_on_workflows_job() -> None:
    from tests.supply_chain._workflows import workflow  # noqa: PLC0415

    job = workflow("ci.yml")["jobs"]["workflows"]
    assert job["permissions"] == {"contents": "read"}
    assert "timeout-minutes" in job
    assert "if" not in job
    assert "needs" not in job
    assert "continue-on-error" not in job
    steps = job["steps"]
    assert steps[0]["with"]["persist-credentials"] is False
    runs = "\n".join(s.get("run", "") for s in steps)
    for needle in (
        "infra/security/check-ci-hardening.sh",
        "scripts/tools.sh install actionlint",
        "--require-hashes",
        "tools/requirements/zizmor.txt",
        'zizmor/bin/zizmor" .github/',
    ):
        assert needle in runs, needle


def test_security_skips_the_sarif_upload_when_the_report_has_no_runs() -> None:
    from tests.supply_chain._workflows import workflow  # noqa: PLC0415

    steps = workflow("security.yml")["jobs"]["advisory"]["steps"]
    check = next(s for s in steps if s.get("id") == "runs")
    assert "jq -e '(.runs | length) > 0'" in check["run"]
    assert "SKIPPED" in check["run"]
    upload = next(s for s in steps if s.get("id") == "sarif")
    assert "steps.runs.outputs.has_runs == 'true'" in upload["if"]
    assert "python3-dev" not in (REPO / ".github/workflows/security.yml").read_text()


def test_zizmor_runs_online_with_a_read_only_token() -> None:
    from tests.supply_chain._workflows import workflow  # noqa: PLC0415

    job = workflow("ci.yml")["jobs"]["workflows"]
    assert job["permissions"] == {"contents": "read"}
    step = next(s for s in job["steps"] if 'zizmor/bin/zizmor" ' in s.get("run", ""))
    assert step["env"] == {"GH_TOKEN": "${{ github.token }}"}
    assert "--offline" not in step["run"]
    assert "--no-online-audits" not in step["run"]


def _plain(text: str) -> str:
    """Drop quotes and turn ${VAR} into $VAR, so brace and no-brace spellings compare equal."""
    return re.sub(r"\$\{(\w+)\}", r"$\1", text).replace('"', "").replace("'", "")


def _writes_to_github_path(line: str, bin_dir: str) -> bool:
    return "GITHUB_PATH" in line and bin_dir in _plain(line)


@pytest.mark.parametrize(
    ("line", "bin_dir", "expected"),
    [
        ('echo "$RUNNER_TEMP/v/bin" >> "$GITHUB_PATH"', "$RUNNER_TEMP/v/bin", True),
        ('echo "${RUNNER_TEMP}/v/bin" >> "$GITHUB_PATH"', "$RUNNER_TEMP/v/bin", True),
        ('echo "${RUNNER_TEMP}/x/bin" >> "$GITHUB_PATH"', "$RUNNER_TEMP/x/bin", True),
        ('echo "$PWD/.tools/bin" >> "$GITHUB_PATH"', "$RUNNER_TEMP/v/bin", False),
    ],
)
def test_github_path_guard_sees_brace_and_plain_spellings(
    line: str, bin_dir: str, *, expected: bool
) -> None:
    assert _writes_to_github_path(line, _plain(bin_dir)) is expected


def test_no_step_puts_a_venv_bin_on_github_path() -> None:
    from tests.supply_chain._workflows import all_files, load  # noqa: PLC0415

    for path in all_files():
        doc = load(path)
        jobs = doc["jobs"].values() if "jobs" in doc else [doc["runs"]]
        for job in jobs:
            runs = "\n".join(s.get("run", "") for s in job.get("steps", []))
            for venv in re.findall(r"python3? -m venv (\S+)", runs):
                bin_dir = _plain(venv) + "/bin"
                for line in runs.splitlines():
                    assert not _writes_to_github_path(line, bin_dir), (
                        path.name,
                        line,
                    )


def test_hardening_lint_runs_with_a_hashed_pyyaml_venv_not_setup_backend() -> None:
    from tests.supply_chain._workflows import workflow  # noqa: PLC0415

    steps = workflow("ci.yml")["jobs"]["workflows"]["steps"]
    assert not any(s.get("uses") == "./.github/actions/setup-backend" for s in steps)
    lint = next(i for i, s in enumerate(steps) if s.get("name") == "CI hardening lint")
    assert any(_installs_pyyaml(s) for s in steps[:lint])
    # The venv's bin is prefixed in the run line itself: never on GITHUB_PATH, never a step env.
    assert (
        steps[lint]["run"].strip()
        == 'PATH="$RUNNER_TEMP/pyyaml/bin:$PATH" infra/security/check-ci-hardening.sh'
    )
    assert "env" not in steps[lint]


def _installs_pyyaml(step: dict[str, str]) -> bool:
    run = step.get("run", "")
    return (
        'python3 -m venv "$RUNNER_TEMP/pyyaml"' in run
        and "--require-hashes" in run
        and "--no-deps" in run
        and "tools/requirements/pyyaml.txt" in run
    )


def test_release_plan_runs_the_matrix_helper_with_a_hashed_pyyaml_venv() -> None:
    from tests.supply_chain._workflows import workflow  # noqa: PLC0415

    steps = workflow("release.yml")["jobs"]["plan"]["steps"]
    plan = next(i for i, s in enumerate(steps) if s.get("id") == "plan")
    assert any(_installs_pyyaml(s) for s in steps[:plan])
    assert (
        '"$RUNNER_TEMP/pyyaml/bin/python" .github/scripts/container_matrix.py' in steps[plan]["run"]
    )
    assert "PATH" not in steps[plan].get("env", {})


@pytest.mark.parametrize("name", ["deploy-dev", "deploy-qa", "deploy-production"])
def test_deploy_jobs_run_the_matrix_helper_with_a_hashed_pyyaml_venv(name: str) -> None:
    from tests.supply_chain._workflows import workflow  # noqa: PLC0415

    steps = workflow("deploy.yml")["jobs"][name]["steps"]
    deploy = next(i for i, s in enumerate(steps) if "deploy-env.sh" in s.get("run", ""))
    assert any(_installs_pyyaml(s) for s in steps[:deploy])
    env = steps[deploy]["env"]
    assert env["MATRIX_CMD"].startswith("${{ runner.temp }}/pyyaml/bin/python ")
    assert env["MATRIX_CMD"].endswith("/.github/scripts/container_matrix.py")
    assert "PATH" not in env


def test_pyyaml_lock_matches_every_other_pyyaml_pin() -> None:
    """Every lock pinning pyyaml agrees with pyyaml.txt (version and hashes), not a literal."""

    def block(text: str) -> tuple[str, set[str]] | None:
        m = re.search(r"^pyyaml==(\S+) \\\n((?:[ \t]+--hash=\S+(?: \\)?\n?)+)", text, re.MULTILINE)
        return (m.group(1), set(re.findall(r"sha256:[0-9a-f]{64}", m.group(2)))) if m else None

    pyyaml_text = (REPO / "tools/requirements/pyyaml.txt").read_text()
    assert re.findall(r"^([A-Za-z0-9_.-]+)==", pyyaml_text, re.MULTILINE) == ["pyyaml"]
    lock = block(pyyaml_text)
    assert lock
    assert lock[1]
    for path in sorted((REPO / "tools/requirements").glob("*.txt")):
        other = block(path.read_text())
        if other is not None:
            assert other == lock, path.name


def test_only_the_sign_attest_job_is_exempt_from_the_environment_rule() -> None:
    root = FIXTURES / "environment-for-secrets" / "fail-reusable-build-other-job"
    out = run_lint(root).stdout
    flagged = re.findall(r"environment-for-secrets job (\S+) ", out)
    assert flagged == ["other"], out


def test_only_the_scorecard_analysis_job_is_exempt_in_scorecard_yml() -> None:
    root = FIXTURES / "environment-for-secrets" / "fail-scorecard-other-job"
    out = run_lint(root).stdout
    flagged = re.findall(r"environment-for-secrets job (\S+) ", out)
    assert flagged == ["other"], out


def test_a_pull_request_caller_of_the_reusable_build_is_flagged() -> None:
    out = run_lint(FIXTURES / "image-push-isolation" / "fail-caller-pull-request").stdout
    assert "pull_request" in out
    assert "image-push-isolation" in out


def test_a_nested_composite_action_is_linted() -> None:
    out = run_lint(FIXTURES / "image-push-isolation" / "fail-composite-nested").stdout
    assert ".github/actions/docker/publish/action.yml:" in out
