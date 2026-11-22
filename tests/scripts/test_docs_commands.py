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
    """The namespaces come from the admin bootstrap (scripts/k8s-bootstrap.sh), never from helm.

    Only tracked docs are read (git ls-files), so nothing outside the public tree is checked.
    """
    offenders = []
    for p in _docs():
        commands = p.read_text("utf-8").replace("\\\n", " ").splitlines()
        if [c for c in commands if "charts/your-service-name" in c and "--create-namespace" in c]:
            offenders.append(str(p.relative_to(ROOT)))
    assert offenders == []


def test_no_doc_says_the_chart_creates_the_namespaces() -> None:
    pattern = re.compile(r"[Tt]he chart creates (them|both|\*\*both\*\*)")
    offenders = [str(p.relative_to(ROOT)) for p in _docs() if pattern.search(p.read_text("utf-8"))]
    assert offenders == []


def test_the_migration_says_the_old_quota_is_recreated_by_the_bootstrap() -> None:
    text = (ROOT / "wiki" / "infrastructure.md").read_text("utf-8")
    section = text[text.index("**An existing release from an older chart**") :]
    section = " ".join(section[: section.index("\n---")].split())
    assert "deletes the old chart's ResourceQuota and LimitRange" in section
    assert "recreates them" in section


def test_the_migration_upgrades_pass_the_current_charts_values_not_the_stored_ones() -> None:
    """The stored values of an older release carry keys the current schema refuses (images.nginx,
    ingress), so reusing them fails the first upgrade (seen on a kind cluster): pass the env's
    values file and the deploy's values instead, in every upgrade of the migration."""
    text = (ROOT / "wiki" / "infrastructure.md").read_text("utf-8")
    section = text[text.index("**An existing release from an older chart**") :]
    section = section[: section.index("\n---")]
    commands = re.findall(r"^helm upgrade .*?(?<!\\)$", section, re.MULTILINE | re.DOTALL)
    upgrades = [" ".join(u.split()) for u in commands]
    assert len(upgrades) == 2, upgrades  # noqa: PLR2004 (keep, then the switch)
    for upgrade in upgrades:
        assert "reuse-values" not in upgrade, upgrade
        assert '-f "charts/your-service-name/values.$ENV.yaml"' in upgrade, upgrade
        assert '"${DEPLOY_VALUES[@]}"' in upgrade, upgrade
