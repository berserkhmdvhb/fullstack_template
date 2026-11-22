# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Docs must not carry the invalid `kubectl create job --from=job/...` (CronJob only)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _docs() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "*.md"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [ROOT / name for name in out.splitlines()]


def test_no_doc_creates_a_job_from_a_job() -> None:
    pattern = re.compile(r"create job[^\n]*--from=job/")
    offenders = [str(p.relative_to(ROOT)) for p in _docs() if pattern.search(p.read_text("utf-8"))]
    assert offenders == []


def test_no_doc_has_a_placeholder_create_job_line() -> None:
    pattern = re.compile(r"kubectl[^\n]*create job \.\.\.")
    offenders = [str(p.relative_to(ROOT)) for p in _docs() if pattern.search(p.read_text("utf-8"))]
    assert offenders == []


def test_no_doc_lets_the_app_chart_create_its_namespace() -> None:
    """The namespaces come from the admin bootstrap (scripts/k8s-bootstrap.sh), never from helm."""
    offenders = []
    for p in _docs():
        if p.relative_to(ROOT).parts[:2] == ("wiki", "plans"):
            continue
        commands = p.read_text("utf-8").replace("\\\n", " ").splitlines()
        if [c for c in commands if "charts/your-service-name" in c and "--create-namespace" in c]:
            offenders.append(str(p.relative_to(ROOT)))
    assert offenders == []
