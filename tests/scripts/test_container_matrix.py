"""Tests for ``.github/scripts/container_matrix.py`` (manifest renderer and version policy)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".github" / "scripts" / "container_matrix.py"
SHA = "0123456789abcdef0123456789abcdef01234567"
RUNNERS = """\
runners:
  linux/amd64: ubuntu-24.04
  linux/arm64: ubuntu-24.04-arm
"""
MANIFEST = (
    RUNNERS
    + """\
schema_version: 1
version:
  baseline: version-file
images:
  - id: backend
    repository: "ghcr.io/${OWNER}/${REPO}/backend"
    dockerfile: Dockerfile.backend
    context: .
    platforms: [linux/amd64, linux/arm64]
    build_args:
      VERSION: "${VERSION}"
  - id: web
    repository: "ghcr.io/${OWNER}/${REPO}/web"
    dockerfile: Dockerfile.web
    context: .
    build_args: {}
"""
)
BASE_ENV = {
    "GITHUB_REPOSITORY_OWNER": "Acme-Org",
    "GITHUB_REPOSITORY": "Acme-Org/My-Service",
    "GITHUB_REF_NAME": "main",
    "GITHUB_REF_TYPE": "branch",
    "GITHUB_SHA": SHA,
}


Runner = Callable[..., "Result"]


class Result:
    def __init__(self, proc: subprocess.CompletedProcess[str], out_file: Path) -> None:
        self.code = proc.returncode
        self.stdout = proc.stdout
        self.stderr = proc.stderr
        self.out_file = out_file

    @property
    def outputs(self) -> dict[str, str]:
        text = self.out_file.read_text() if self.out_file.exists() else ""
        return dict(line.split("=", 1) for line in text.splitlines())


@pytest.fixture
def run(tmp_path: Path) -> Runner:
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "container-images.yml").write_text(MANIFEST)
    out_file = tmp_path / "out.txt"

    def _run(
        *args: str,
        env: dict[str, str | None] | None = None,
        version_file: str | None = None,
        manifest: str | None = None,
        with_runners: bool = True,
        use_output_file: bool = True,
    ) -> Result:
        if version_file is not None:
            (tmp_path / "VERSION").write_text(version_file)
        if manifest is not None:
            head = RUNNERS if with_runners and "runners:" not in manifest else ""
            (tmp_path / ".github" / "container-images.yml").write_text(head + manifest)
        full: dict[str, str] = {**BASE_ENV, "PATH": "/usr/bin:/bin"}
        if use_output_file:
            full["GITHUB_OUTPUT"] = str(out_file)
        for key, val in (env or {}).items():
            if val is None:
                full.pop(key, None)
            else:
                full[key] = val
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(tmp_path), *args],
            env=full,
            capture_output=True,
            text=True,
            check=False,
        )
        return Result(proc, out_file)

    return _run


def refused(res: Result, needle: str) -> None:
    assert res.code != 0
    assert "Traceback" not in res.stderr
    assert needle in res.stderr


def test_main_branch_policy(run: Runner) -> None:
    res = run("--matrix", version_file="1.2.3\n")
    assert res.code == 0, res.stderr
    assert {k: v for k, v in res.outputs.items() if k != "platform_matrix"} == {
        "version": "1.2.3",
        "channel": "main",
        "image_tag": "1.2.3-main-0123456",
        "immutable": "false",
        "arm64": "false",
    }


def test_missing_version_file_defaults_to_zero(run: Runner) -> None:
    assert run("--matrix").outputs["image_tag"] == "0.0.0-main-0123456"


def test_tag_overrides_baseline(run: Runner) -> None:
    env = {"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "v2.0.1"}
    res = run("--matrix", env=env, version_file="1.2.3")
    assert {k: v for k, v in res.outputs.items() if k != "platform_matrix"} == {
        "version": "2.0.1",
        "channel": "prod",
        "image_tag": "2.0.1-prod-0123456",
        "immutable": "true",
        "arm64": "false",
    }


def test_tag_with_build_metadata_is_docker_safe(run: Runner) -> None:
    env = {"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "v2.0.1-rc.1+build5"}
    assert run("--matrix", env=env).outputs["version"] == "2.0.1-rc.1-build5"


@pytest.mark.parametrize("tag", ["2.0.1", "v2", "vx.y.z", "v1.2.3;rm", "v1.2.3\n"])
def test_bad_tag_refused(run: Runner, tag: str) -> None:
    env = {"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": tag}
    refused(run("--matrix", env=env), "tag")


def test_other_branch_uses_sanitised_channel(run: Runner) -> None:
    env = {"GITHUB_REF_NAME": "feature/Add Thing_with-a-very-long-name"}
    res = run("--matrix", env=env, version_file="0.3.0")
    assert res.outputs["channel"] == "feature-Add-Thing_wi"
    assert res.outputs["image_tag"] == "0.3.0-feature-Add-Thing_wi-0123456"
    assert res.outputs["immutable"] == "false"


def test_pull_request_ref_is_a_branch_channel(run: Runner) -> None:
    assert run("--matrix", env={"GITHUB_REF_NAME": "12/merge"}).outputs["channel"] == "12-merge"


@pytest.mark.parametrize("content", ["not-a-version", "1.2", "v1.2.3", "1.2.3 4", ""])
def test_bad_version_file_refused(run: Runner, content: str) -> None:
    refused(run("--matrix", version_file=content), "VERSION")


@pytest.mark.parametrize("name", ["GITHUB_REF_NAME", "GITHUB_REF_TYPE", "GITHUB_SHA"])
def test_missing_ref_env_refused(run: Runner, name: str) -> None:
    refused(run("--matrix", env={name: None}), name)


def test_bad_sha_refused(run: Runner) -> None:
    refused(run("--matrix", env={"GITHUB_SHA": "xyz"}), "GITHUB_SHA")


def test_bad_ref_type_refused(run: Runner) -> None:
    refused(run("--matrix", env={"GITHUB_REF_TYPE": "other"}), "GITHUB_REF_TYPE")


def test_matrix_shape_and_repository_lowercased(run: Runner) -> None:
    res = run("--matrix", version_file="1.2.3")
    matrix = json.loads(res.stdout.splitlines()[0])
    assert list(matrix) == ["include"]
    first = matrix["include"][0]
    assert list(first) == ["id", "repository", "dockerfile", "context", "build_args", "platforms"]
    assert first == {
        "id": "backend",
        "repository": "ghcr.io/acme-org/my-service/backend",
        "dockerfile": "Dockerfile.backend",
        "context": ".",
        "build_args": {"VERSION": "1.2.3"},
        "platforms": ["linux/amd64"],
    }
    assert [i["id"] for i in matrix["include"]] == ["backend", "web"]


@pytest.mark.parametrize("name", ["GITHUB_REPOSITORY_OWNER", "GITHUB_REPOSITORY"])
def test_missing_owner_or_repo_refused(run: Runner, name: str) -> None:
    refused(run("--matrix", env={name: None}), name)


def test_outputs_go_to_stdout_without_github_output(run: Runner) -> None:
    res = run("--matrix", use_output_file=False)
    assert res.code == 0
    assert "image_tag=0.0.0-main-0123456" in res.stdout.splitlines()
    assert not res.out_file.exists()


def test_github_output_is_appended(run: Runner) -> None:
    first = run("--matrix")
    first.out_file.write_text("earlier=1\n")
    res = run("--matrix")
    assert res.outputs["earlier"] == "1"
    assert res.outputs["version"] == "0.0.0"


def test_multiline_version_file_refused_by_the_version_validator(run: Runner) -> None:
    # This hits the VERSION validator; test_emit_refuses_newlines covers format_outputs.
    refused(run("--matrix", version_file="1.2.3\n9.9.9"), "VERSION")


def test_emit_refuses_newlines() -> None:
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        import container_matrix  # noqa: PLC0415
    finally:
        sys.path.remove(str(SCRIPT.parent))
    with pytest.raises(container_matrix.InputError, match="newline"):
        container_matrix.format_outputs({"version": "1\nx=y"})


def test_deploy_matrix_select(run: Runner) -> None:
    res = run("--deploy-matrix", "--select", "web")
    assert res.code == 0, res.stderr
    ids = [i["id"] for i in json.loads(res.stdout)["include"]]
    assert ids == ["web"]
    res = run("--deploy-matrix", "--select", "web, backend")
    assert [i["id"] for i in json.loads(res.stdout)["include"]] == ["web", "backend"]


def test_deploy_matrix_unknown_id(run: Runner) -> None:
    refused(run("--deploy-matrix", "--select", "backend,nope"), "nope")


@pytest.mark.parametrize("sel", ["", " , "])
def test_deploy_matrix_empty_selection(run: Runner, sel: str) -> None:
    refused(run("--deploy-matrix", "--select", sel), "empty")


def test_deploy_matrix_requires_select(run: Runner) -> None:
    assert run("--deploy-matrix").code != 0


@pytest.mark.parametrize(
    ("manifest", "needle"),
    [
        ("images: [", "manifest"),
        ("images:\n  - {id: a, repository: r, dockerfile: d, platforms: []}\n", "platforms"),
        ("images:\n  - {id: a, repository: r, dockerfile: d, platforms: [arm64]}\n", "platforms"),
        ("images:\n  - {id: a, repository: r, dockerfile: d, platforms: x}\n", "platforms"),
        (
            "images:\n  - {id: a, repository: r, dockerfile: d, platforms: [linux/s390x]}\n",
            "no runner for 'linux/s390x'",
        ),
        ("- just\n- a list\n", "manifest"),
        ("schema_version: 1\n", "images"),
        ("images: []\n", "images"),
        ("surprise: 1\nimages:\n  - {id: a, repository: r, dockerfile: d}\n", "surprise"),
        ("images:\n  - {id: a, repository: r, dockerfile: d, colour: red}\n", "colour"),
        (
            (
                "images:\n  - {id: a, repository: r, dockerfile: d}\n"
                "  - {id: a, repository: r, dockerfile: d}\n"
            ),
            "duplicate",
        ),
        ("images:\n  - {id: Bad_ID, repository: r, dockerfile: d}\n", "Bad_ID"),
        ("images:\n  - {id: a, repository: r}\n", "dockerfile"),
        ("images:\n  - {id: a, repository: '${NOPE}/x', dockerfile: d}\n", "NOPE"),
        ("images:\n  - {id: a, repository: r, dockerfile: d, build_args: [x]}\n", "build_args"),
        (
            "images:\n  - {id: a, repository: r, dockerfile: d, distroless: yes please}\n",
            "distroless",
        ),
        ("images:\n  - {id: a, repository: r, dockerfile: d, run_env: [x]}\n", "run_env"),
        ("images:\n  - {id: a, repository: r, dockerfile: d, run_env: {K: 1}}\n", "run_env"),
        ("images:\n  - {id: a, repository: r, dockerfile: d, run_args: x}\n", "run_args"),
        (
            "images:\n  - {id: waf, repository: r, dockerfile: d, healthcheck_exempt: true}\n",
            "healthcheck_exempt",
        ),
        (
            "images:\n  - {id: waf, repository: r, dockerfile: d, healthcheck_exempt: '  '}\n",
            "healthcheck_exempt",
        ),
    ],
)
def test_bad_manifest_refused(run: Runner, manifest: str, needle: str) -> None:
    refused(run("--matrix", manifest=manifest), needle)


@pytest.mark.parametrize("image_id", ["backend", "frontend", "a"])
def test_only_the_waf_may_be_exempt_from_the_healthcheck(run: Runner, image_id: str) -> None:
    manifest = (
        f"images:\n  - {{id: {image_id}, repository: r, dockerfile: d,"
        " healthcheck_exempt: flaky}\n"
    )
    refused(run("--matrix", manifest=manifest), "healthcheck_exempt")


def test_the_two_exemption_allowlists_agree() -> None:
    sys.path.insert(0, str(SCRIPT.parent))
    import container_matrix  # noqa: PLC0415

    shell = (REPO_ROOT / "scripts/ci/image-checks.sh").read_text(encoding="utf-8")
    match = re.search(r"^HEALTHCHECK_EXEMPT_IDS=\((.*)\)$", shell, re.MULTILINE)
    assert match
    assert set(match[1].split()) == set(container_matrix.HEALTHCHECK_EXEMPT_IDS) == {"waf"}


def test_image_check_settings_are_accepted_and_not_rendered(run: Runner) -> None:
    manifest = (
        "images:\n  - {id: waf, repository: r, dockerfile: d, distroless: true,"
        " run_env: {K: v}, run_args: ['--read-only'],"
        " healthcheck_exempt: probed by Kubernetes only}\n"
    )
    res = run("--matrix", manifest=manifest)
    assert res.code == 0, res.stderr
    entry = json.loads(res.stdout.splitlines()[0])["include"][0]
    assert set(entry) == {"id", "repository", "dockerfile", "context", "build_args", "platforms"}


def test_missing_manifest_refused(run: Runner, tmp_path: Path) -> None:
    (tmp_path / ".github" / "container-images.yml").unlink()
    refused(run("--matrix"), "container-images.yml")


def test_repo_manifest_renders() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(REPO_ROOT), "--matrix"],
        env={**BASE_ENV, "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    ids = [i["id"] for i in json.loads(proc.stdout.splitlines()[0])["include"]]
    assert ids == ["backend", "frontend", "waf"]


FORBIDDEN_RUN_ARGS = [
    "-p",
    "--publish",
    "-P",
    "--publish-all",
    "--privileged",
    "-v",
    "--volume",
    "--mount",
    "--network",
    "--net",
    "--pid",
    "--ipc",
    "--cap-add",
    "--device",
    "--security-opt",
]


def _forbidden_pairs() -> list[tuple[str, str]]:
    """Each flag with the forms it can take (long: separate or `=`; short: separate or attached)."""
    forms = {True: ("separate", "equals"), False: ("separate", "attached")}
    return [(f, form) for f in FORBIDDEN_RUN_ARGS for form in forms[f.startswith("--")]]


@pytest.mark.parametrize(("flag", "form"), _forbidden_pairs())
def test_run_args_that_widen_the_container_are_refused(run: Runner, flag: str, form: str) -> None:
    args = {"separate": [flag, "x"], "equals": [f"{flag}=x"], "attached": [f"{flag}x"]}[form]
    manifest = (
        f"images:\n  - {{id: a, repository: r, dockerfile: d, run_args: {json.dumps(args)}}}\n"
    )
    refused(run("--matrix", manifest=manifest), "run_args")


def test_harmless_run_args_are_accepted(run: Runner) -> None:
    manifest = (
        "images:\n  - {id: a, repository: r, dockerfile: d,"
        " run_args: ['--read-only', '--tmpfs', '/tmp']}\n"
    )
    assert run("--matrix", manifest=manifest).code == 0


def _platform_matrix(res: Result) -> list[dict[str, object]]:
    assert res.code == 0, res.stderr
    return cast("list[dict[str, object]]", json.loads(res.outputs["platform_matrix"])["include"])


def test_unset_switch_builds_amd64_only_on_its_native_runner(run: Runner) -> None:
    res = run("--matrix", version_file="1.2.3")
    assert res.outputs["arm64"] == "false"
    legs = _platform_matrix(res)
    assert [(e["id"], e["platform"], e["runner"], e["slug"]) for e in legs] == [
        ("backend", "linux/amd64", "ubuntu-24.04", "linux-amd64"),
        ("web", "linux/amd64", "ubuntu-24.04", "linux-amd64"),
    ]
    assert "arm64" not in json.dumps(json.loads(res.stdout.splitlines()[0]))


@pytest.mark.parametrize("value", ["", "false", "TRUE", "1", "yes"])
def test_anything_but_true_keeps_arm64_off(run: Runner, value: str) -> None:
    res = run("--matrix", env={"BUILD_ARM64": value})
    assert res.outputs["arm64"] == "false"
    assert {e["platform"] for e in _platform_matrix(res)} == {"linux/amd64"}


def test_the_switch_adds_arm64_on_its_native_runner(run: Runner) -> None:
    res = run("--matrix", env={"BUILD_ARM64": "true"}, version_file="1.2.3")
    assert res.outputs["arm64"] == "true"
    legs = _platform_matrix(res)
    assert [(e["id"], e["platform"], e["runner"], e["slug"]) for e in legs] == [
        ("backend", "linux/amd64", "ubuntu-24.04", "linux-amd64"),
        ("backend", "linux/arm64", "ubuntu-24.04-arm", "linux-arm64"),
        ("web", "linux/amd64", "ubuntu-24.04", "linux-amd64"),
    ]
    first = json.loads(res.stdout.splitlines()[0])["include"][0]
    assert first["platforms"] == ["linux/amd64", "linux/arm64"]
    assert legs[1]["repository"] == "ghcr.io/acme-org/my-service/backend"
    assert legs[1]["build_args"] == {"VERSION": "1.2.3"}
    assert "platforms" not in legs[1]


def test_arm64_output_is_true_only_when_an_arm64_leg_is_rendered(run: Runner) -> None:
    manifest = "images:\n  - {id: a, repository: r, dockerfile: d}\n"
    res = run("--matrix", manifest=manifest, env={"BUILD_ARM64": "true"})
    assert res.outputs["arm64"] == "false"  # the switch is on, but no image lists linux/arm64
    assert {e["platform"] for e in _platform_matrix(res)} == {"linux/amd64"}


def test_a_missing_runners_map_is_refused(run: Runner) -> None:
    manifest = "images:\n  - {id: a, repository: r, dockerfile: d}\n"
    refused(run("--matrix", manifest=manifest, with_runners=False), "runners")


def test_an_image_without_amd64_cannot_build_with_the_switch_unset(run: Runner) -> None:
    manifest = "images:\n  - {id: a, repository: r, dockerfile: d, platforms: [linux/arm64]}\n"
    refused(run("--matrix", manifest=manifest), "linux/amd64")
    assert run("--matrix", manifest=manifest, env={"BUILD_ARM64": "true"}).code == 0


def test_the_repo_manifest_lists_both_platforms_and_its_runner_map() -> None:
    env = {**BASE_ENV, "PATH": "/usr/bin:/bin", "BUILD_ARM64": "true"}
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(REPO_ROOT), "--matrix"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = dict(line.split("=", 1) for line in proc.stdout.splitlines()[1:])
    legs = json.loads(out["platform_matrix"])["include"]
    assert {(e["id"], e["platform"], e["runner"]) for e in legs} == {
        (i, p, r)
        for i in ("backend", "frontend", "waf")
        for p, r in (("linux/amd64", "ubuntu-24.04"), ("linux/arm64", "ubuntu-24.04-arm"))
    }
