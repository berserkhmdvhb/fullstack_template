"""The docs site's workflows: the pull request build in ci.yml and the Pages deploy (offline)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.supply_chain._workflows import workflow
from tests.supply_chain.test_required_checks import _all, _gate
from tests.supply_chain.test_workflows_frontend import _run_filter

BASH: str = shutil.which("bash") or "/usr/bin/bash"
UPLOAD_PAGES = "actions/upload-pages-artifact@fc324d3547104276b827a68afc52ff2a11cc49c9 # v5.0.0"
DEPLOY_PAGES = "actions/deploy-pages@368f82528645a54fb793d4d04e342629a3f51346 # v5.0.1"


def _build() -> dict[str, Any]:
    return dict(workflow("reusable-docs-site.yml")["jobs"]["build"])


def _step(job: dict[str, Any], name: str) -> dict[str, Any]:
    return dict(next(s for s in job["steps"] if s.get("name") == name))


def _raw(name: str) -> str:
    return (Path(__file__).resolve().parents[2] / ".github/workflows" / name).read_text("utf-8")


def test_the_build_is_read_only_uncached_and_strict() -> None:
    job = _build()
    assert job["permissions"] == {"contents": "read"}
    assert "continue-on-error" not in job
    node = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/setup-node@"))
    assert node["with"]["package-manager-cache"] is False
    assert "tools/pins.tsv" in _step_by_id(job, "pin")["run"]
    assert _step(job, "Install the site dependencies")["run"] == "npm ci --ignore-scripts"
    names = [s.get("name") for s in job["steps"]]
    assert names.index("Resolver unit tests") < names.index("Build the site")
    assert names.index("Build the site") < names.index("axe accessibility scan")
    assert _step(job, "Resolver unit tests")["run"] == "npm test"
    assert job["env"]["SITE_REPOSITORY"] == "${{ github.repository }}"
    assert job["env"]["SITE_URL"] == "${{ inputs.site-url }}"
    assert not any(str(s.get("uses", "")).startswith("actions/cache") for s in job["steps"])


def test_the_site_guards_run_on_the_pull_requests_that_change_the_site() -> None:
    """The backend filter leaves a docs-only change out, so the docs-site call runs them."""
    job = workflow("reusable-docs-site.yml")["jobs"]["guards"]
    assert job["permissions"] == {"contents": "read"}
    assert job["if"] == "${{ !inputs.upload-pages }}"
    assert "./.github/actions/setup-backend" in [s.get("uses") for s in job["steps"]]
    run = _step(job, "Docs site guards")["run"]
    assert "tests/docs_site" in run
    assert "tests/test_adr_index.py" in run


def _step_by_id(job: dict[str, Any], step_id: str) -> dict[str, Any]:
    return dict(next(s for s in job["steps"] if s.get("id") == step_id))


def test_only_the_deploy_run_uploads_the_pages_artifact() -> None:
    upload = _step(_build(), "Upload the Pages artifact")
    assert upload["if"] == "inputs.upload-pages"
    assert upload["with"]["path"] == "docs-site/dist"
    assert (
        workflow("reusable-docs-site.yml")["on"]["workflow_call"]["inputs"]["upload-pages"][
            "default"
        ]
        is False
    )
    assert f"uses: {UPLOAD_PAGES}" in _raw("reusable-docs-site.yml")


def test_every_pull_request_that_touches_the_site_builds_it_behind_ci_ok() -> None:
    jobs = workflow("ci.yml")["jobs"]
    job = jobs["docs-site"]
    assert job["uses"] == "./.github/workflows/reusable-docs-site.yml"
    assert job["if"] == "needs.changes.outputs.docs == 'true'"
    assert job["permissions"] == {"contents": "read"}
    assert "with" not in job  # never uploads a Pages artifact from ci.yml
    assert "docs-site" in jobs["ci-ok"]["needs"]


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is needed to run the gate script")
def test_the_gate_lets_docs_site_skip_on_a_pull_request_only() -> None:
    assert _gate(_all(**{"docs-site": "skipped"})) == 0
    assert _gate(_all(**{"docs-site": "skipped"}), "push") != 0
    assert _gate(_all(**{"docs-site": "failure"})) != 0


@pytest.mark.parametrize(
    ("path", "docs"),
    [
        ("docs-site/src/content/docs/index.md", True),
        ("wiki/ci-cd.md", True),
        ("CONTRIBUTING.md", True),
        ("CONVENTIONS.md", True),
        ("tools/pins.tsv", True),
        (".github/workflows/docs-site.yml", True),
        ("scripts/ci/retry.sh", True),
        ("src/your_service_name/main.py", False),
        ("charts/your-service-name/values.yaml", False),
        ("frontend/app/page.tsx", False),
    ],
)
def test_pull_request_paths_map_to_the_docs_output(
    tmp_path: Path, path: str, *, docs: bool
) -> None:
    assert _run_filter(tmp_path, "pull_request", [path])["docs"] == str(docs).lower()


def test_a_deleted_file_runs_the_docs_site(tmp_path: Path) -> None:
    """A page may link any tracked file, so a deletion or rename anywhere rebuilds the site."""
    result = _run_filter(tmp_path, "pull_request", ["src/x.py"], removed=("charts/c/values.yaml",))
    assert result["docs"] == "true"


def test_the_deploy_runs_from_main_only_never_on_a_pull_request() -> None:
    doc = workflow("docs-site.yml")
    assert set(doc["on"]) == {"push", "workflow_dispatch"}
    assert doc["on"]["push"]["branches"] == ["main"]
    paths = set(doc["on"]["push"]["paths"])
    assert {"docs-site/**", "wiki/**", "CONTRIBUTING.md", "tools/pins.tsv"} <= paths
    assert {".github/workflows/docs-site.yml", ".github/workflows/reusable-docs-site.yml"} <= paths
    assert doc["permissions"] == {}
    assert doc["concurrency"]["cancel-in-progress"] is False
    pages = doc["jobs"]["pages"]
    assert pages["if"] == "github.event.repository.fork == false && github.ref == 'refs/heads/main'"
    assert pages["permissions"] == {"contents": "read", "pages": "read"}


def test_only_the_deploy_job_holds_pages_and_id_token_in_github_pages() -> None:
    jobs = workflow("docs-site.yml")["jobs"]
    deploy = jobs["deploy"]
    assert deploy["permissions"] == {"contents": "read", "pages": "write", "id-token": "write"}
    assert deploy["environment"]["name"] == "github-pages"
    assert deploy["needs"] == "build"
    for name, job in jobs.items():
        if name != "deploy":
            assert "write" not in job["permissions"].values(), name
    assert jobs["build"]["with"] == {
        "upload-pages": True,
        "site-url": "${{ needs.pages.outputs.url }}",
    }
    assert jobs["build"]["if"] == "needs.pages.outputs.enabled == 'true'"
    assert f"uses: {DEPLOY_PAGES}" in _raw("docs-site.yml")


def _pages_check(tmp_path: Path, gh_body: str) -> tuple[int, str, str]:
    """Run the `pages` job's script with a stub gh; returns (exit code, its output file, stdout)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(f"#!/usr/bin/env bash\n{gh_body}\n")
    (bin_dir / "gh").chmod(0o755)
    out = tmp_path / "output"
    out.write_text("")
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(out),
        "RUNNER_TEMP": str(tmp_path),
        "REPO": "o/r",
    }
    script = workflow("docs-site.yml")["jobs"]["pages"]["steps"][0]["run"]
    done = subprocess.run(
        [BASH, "-e", "-c", script], env=env, capture_output=True, text=True, check=False
    )
    return done.returncode, out.read_text(), done.stdout


def test_pages_built_by_actions_deploys_at_its_published_url(tmp_path: Path) -> None:
    code, out, _ = _pages_check(tmp_path, "printf 'workflow\\thttps://docs.example.com/\\n'")
    assert (code, out) == (0, "enabled=true\nurl=https://docs.example.com/\n")


@pytest.mark.parametrize(
    "gh_body",
    ["echo 'gh: Not Found (HTTP 404)' >&2; exit 1", "printf 'legacy\\thttps://o.github.io/r/\\n'"],
    ids=["off", "branch"],
)
def test_pages_off_or_built_from_a_branch_is_a_green_no_op(tmp_path: Path, gh_body: str) -> None:
    code, out, stdout = _pages_check(tmp_path, gh_body)
    assert (code, out) == (0, "enabled=false\n")
    assert "::notice" in stdout
    assert "scripts/github-settings.sh" in stdout


def test_another_pages_api_error_fails_the_run(tmp_path: Path) -> None:
    code, out, _ = _pages_check(tmp_path, "echo 'gh: Server Error (HTTP 502)' >&2; exit 1")
    assert code != 0
    assert out == ""
