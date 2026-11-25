"""Every shipped deploy path's Helm wait ends before the backend's rollout progress deadline.

A deploy that keeps a failed release (no --rollback-on-failure) and waited as long as
`rollout.progressDeadlineSeconds` leaves its Deployment `ProgressDeadlineExceeded`, and the retry
fails on that at once (wiki/database.md, "Proven on kind"). So each wait is below the deadline. The
one exception is a wait that rolls back on failure: scripts/ci/deploy-env.sh's upgrade
(--rollback-on-failure) uninstalls a failed first install and rolls a failed upgrade back, so no
retry meets the stale condition; it takes deploy.yml's HELM_TIMEOUT, which is still below.

Static, offline.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DEADLINE = int(
    yaml.safe_load((ROOT / "charts/your-service-name/values.yaml").read_text())["rollout"][
        "progressDeadlineSeconds"
    ]
)
UNIT = {"s": 1, "m": 60, "h": 3600}
DURATION = r"(?:\d+[hms])+"


def _seconds(duration: str) -> int:
    return sum(int(n) * UNIT[u] for n, u in re.findall(r"(\d+)([hms])", duration))


def _waits() -> list[tuple[str, str]]:
    """(where, duration) of every Helm wait of a deploy path: the workflows' HELM_TIMEOUT defaults
    (deploy-env.sh's upgrade and rollback, deploy-ops.yml's rollback, preview.yml's deploy and
    preview-cleanup.sh's uninstall), preview-cleanup.sh's own default, and each literal --timeout of
    an install, upgrade or rollback in the kind deploy, the bootstrap migration and the kind tests,
    and a kind-test helper's default `timeout: str = "<d>"` (a `helm test`, which deploys nothing,
    is not one)."""
    found = []
    for wf in ("deploy.yml", "deploy-ops.yml", "preview.yml"):
        text = (ROOT / ".github" / "workflows" / wf).read_text()
        defaults = re.findall(r"vars\.HELM_TIMEOUT \|\| '([^']+)'", text)
        assert defaults, wf
        found += [(wf, d) for d in defaults]
    cleanup = (ROOT / "scripts/ci/preview-cleanup.sh").read_text()
    found += [
        ("preview-cleanup.sh", d) for d in re.findall(rf"HELM_TIMEOUT:-({DURATION})\}}", cleanup)
    ]
    bootstrap = (ROOT / "scripts/migrate-to-bootstrap.sh").read_text()
    found += [
        ("migrate-to-bootstrap.sh", d) for d in re.findall(rf"timeout=({DURATION}) ", bootstrap)
    ]
    files = [ROOT / "scripts/kind-deploy.sh", *sorted((ROOT / "tests/k8s").glob("*.py"))]
    for path in files:
        for line in path.read_text().splitlines():
            if re.search(r"\b(helm_as|helm) test\b", line):
                continue
            for d in re.findall(rf"--timeout[ =\"', ]+({DURATION})\b", line):
                found.append((path.name, d))
            # a helper's default wait, passed on as "--timeout", timeout
            for d in re.findall(rf"\btimeout: str = \"({DURATION})\"", line):
                found.append((path.name, d))
    return found


def test_the_waits_are_found() -> None:
    where = {w for w, _ in _waits()}
    assert {"deploy.yml", "deploy-ops.yml", "preview.yml", "preview-cleanup.sh"} <= where
    assert {"kind-deploy.sh", "migrate-to-bootstrap.sh"} <= where
    assert "_kube.py" in where


@pytest.mark.parametrize(("where", "duration"), _waits())
def test_each_wait_is_below_the_progress_deadline(where: str, duration: str) -> None:
    assert _seconds(duration) < DEADLINE, (
        f"{where}: a Helm wait of {duration} is not below rollout.progressDeadlineSeconds "
        f"({DEADLINE} s); a retry after a failed first install would fail at once"
    )


def test_the_one_path_that_may_wait_as_long_rolls_back_on_failure() -> None:
    """deploy-env.sh's upgrade waits HELM_TIMEOUT with --rollback-on-failure (see the module)."""
    script = (ROOT / "scripts/ci/deploy-env.sh").read_text()
    flags = (
        "--rollback-on-failure --server-side=true --force-conflicts --wait --wait-for-jobs "
        '--timeout "$HELM_TIMEOUT"'
    )
    assert flags in script
