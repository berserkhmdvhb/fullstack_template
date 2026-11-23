# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Browser CSP reports from the built frontend reach the backend's log, on the kind cluster fst
(wiki/security.md, CSP Violation Reports).

Run with ``make kind-csp-report-test`` after ``make kind-up kind-deploy`` (which turns
``cspReporting.enabled`` on for its release) and with the frontend's Playwright browser installed;
``make test`` never collects it (tests/conftest.py ignores tests/k8s). It talks to fst only,
through .local/kind/kubeconfig, and refuses any other context. It runs one Playwright spec
(frontend/e2e/csp-report.spec.ts) through scripts/ci/kind-forward.sh against the release's public
forward (frontend, WAF, backend), with a fresh marker in the page's path, then finds the backend's
warning for that violation in the backend Pods' logs. It changes nothing on the release.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import time

from tests.k8s._kube import KUBECONFIG, RELEASE, ROOT, Kube

SPEC = "e2e/csp-report.spec.ts"
LOG_WAIT = 30.0  # seconds for the warning to show in the log after the report was delivered


def test_a_browser_csp_violation_is_logged_by_the_backend() -> None:
    assert KUBECONFIG.is_file(), f"no {KUBECONFIG}: run make kind-up kind-deploy first"
    kube = Kube()
    marker = f"fst{secrets.token_hex(8)}"
    started = time.time()
    forward = ROOT / "scripts" / "ci" / "kind-forward.sh"
    cmd = [str(forward), "npx", "playwright", "test", SPEC, "--retries=0"]
    env = {**os.environ, "CSP_REPORT_MARKER": marker}
    done = subprocess.run(cmd, cwd=ROOT / "frontend", env=env, check=False)
    print(f"playwright: rc {done.returncode}, {time.time() - started:.0f}s")
    assert done.returncode == 0, "the browser's report was not delivered (the spec's output above)"
    selector = f"app.kubernetes.io/component=backend,app.kubernetes.io/instance={RELEASE}"
    end = time.time() + LOG_WAIT
    while True:
        log = kube("logs", "-l", selector, "--tail=-1", "--prefix")
        lines = [ln for ln in log.splitlines() if marker in ln]
        if lines or time.time() >= end:
            break
        time.sleep(1)
    print("\n".join(lines))
    assert lines, f"no backend log line names the marker {marker}"
    assert all("CSP violation" in ln and "script-src" in ln for ln in lines), lines
