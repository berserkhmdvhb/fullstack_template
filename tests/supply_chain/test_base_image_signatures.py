"""Base-image signatures are verified before the build (PR images job, release build)."""

from __future__ import annotations

from typing import Any, cast

from tests.supply_chain._workflows import REPO, workflow

SCRIPT = "infra/security/local/verify-base-images.sh"
UNSIGNED = REPO / "infra" / "security" / "local" / "unsigned-base-images.tsv"


def _steps(name: str, job: str) -> list[dict[str, Any]]:
    return cast("list[dict[str, Any]]", workflow(name)["jobs"][job]["steps"])


def _index(steps: list[dict[str, Any]], needle: str) -> int:
    hits = [
        i for i, s in enumerate(steps) if needle in s.get("run", "") or needle in s.get("uses", "")
    ]
    assert hits, needle
    return hits[0]


def _check_before_build(name: str, job: str, build: str) -> None:
    steps = _steps(name, job)
    verify = _index(steps, SCRIPT)
    assert _index(steps, "tools.sh install cosign") < verify < _index(steps, build)


def test_pull_request_images_job_verifies_before_the_build() -> None:
    _check_before_build("ci.yml", "images", "build-image.sh")


def test_release_build_verifies_before_the_build() -> None:
    _check_before_build("reusable-build.yml", "build", "docker/build-push-action")


def test_verification_needs_no_new_grant() -> None:
    ci = workflow("ci.yml")["jobs"]["images"]
    assert ci["permissions"] == {"contents": "read"}
    assert "pull_request" in ci["if"]
    build = workflow("reusable-build.yml")["jobs"]["build"]
    assert build["permissions"] == {"contents": "read", "packages": "write"}


def test_every_unsigned_base_has_a_reason() -> None:
    rows = [
        r.split("\t")
        for r in UNSIGNED.read_text(encoding="utf-8").splitlines()
        if r and r[0] != "#"
    ]
    assert {r[0] for r in rows} == {"debian", "node", "envoyproxy/envoy", "ghcr.io/astral-sh/uv"}
    assert all(len(r) > 1 and r[1].strip() for r in rows)
