"""Shared loader and rules for the GitHub Actions workflow tests (static, offline)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import yaml

REPO = Path(__file__).resolve().parent.parent.parent
WORKFLOWS = REPO / ".github" / "workflows"
ACTIONS = REPO / ".github" / "actions"
DOCKER_PIN = re.compile(r"^docker://\S+@sha256:[0-9a-f]{64}$")
SHA_PIN = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}\s+# v\d+(\.\d+)*\S*$")


def load(path: Path) -> dict[str, Any]:
    """Parse a workflow or action file (PyYAML reads the key `on` as True)."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if True in data:
        data["on"] = data.pop(True)
    return cast("dict[str, Any]", data)


def workflow(name: str) -> dict[str, Any]:
    return load(WORKFLOWS / name)


def all_files() -> list[Path]:
    return sorted([*WORKFLOWS.glob("*.yml"), *ACTIONS.glob("*/action.yml")])


def steps_of(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Every step of every job, or of a composite action."""
    if "jobs" in doc:
        return [s for j in doc["jobs"].values() for s in j.get("steps", [])]
    return cast("list[dict[str, Any]]", doc["runs"]["steps"])


def unpinned_uses(path: Path) -> list[str]:
    """`uses:` lines of third-party actions that lack a 40-hex SHA plus a version comment."""
    bad = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*(?:-\s+)?uses:\s*(.+)$", line)
        if not m:
            continue
        ref = m[1].strip()
        if ref.startswith("./"):
            continue  # a local reference: the same commit
        pinned = DOCKER_PIN.match(ref) if ref.startswith("docker://") else SHA_PIN.match(ref)
        if not pinned:
            bad.append(line.strip())
    return bad


def every_step() -> list[tuple[str, dict[str, Any]]]:
    """(file name, step) for every workflow and composite action step."""
    return [
        (p.name if p.parent == WORKFLOWS else p.parent.name, s)
        for p in all_files()
        for s in steps_of(load(p))
    ]


ARTIFACT_RETENTION_DAYS = 90  # release, scan and report artifacts
FAILURE_ARTIFACT_RETENTION_DAYS = 7


def failure_uploads(job: dict[str, Any]) -> list[dict[str, Any]]:
    """The steps of a job that upload an artifact only on failure, kept for 7 days."""
    return [
        s
        for s in job["steps"]
        if str(s.get("uses", "")).startswith("actions/upload-artifact@")
        and s.get("if") == "failure()"
        and s["with"].get("retention-days") == FAILURE_ARTIFACT_RETENTION_DAYS
    ]
