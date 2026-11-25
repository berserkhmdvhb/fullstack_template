# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Browser CSP reports from the built frontend reach the backend's log, on the kind cluster fst
(wiki/security.md, CSP Violation Reports).

Run with ``make kind-csp-report-test`` after ``make kind-up kind-deploy`` and with the frontend's
Playwright browser installed; ``make test`` never collects it (tests/conftest.py ignores
tests/k8s). It talks to fst only, through .local/kind/kubeconfig, and refuses any other context.
For the module's run it upgrades the release with ``cspReporting.enabled`` (as the deployer,
``--reuse-values``) and rolls it back after, on every exit path (_kube.upgraded), so the steps after
it see the release as kind-deploy left it. Through scripts/ci/kind-forward.sh's public forward
(frontend, WAF, backend) it runs one Playwright spec (frontend/e2e/csp-report.spec.ts) with a fresh
marker in the page's path and finds the backend's warning for that violation in the backend Pods'
logs; then it checks the WAF's report-route exception stays narrow: each request outside it is 403.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
from collections.abc import Iterator

import pytest

from tests.k8s._kube import (
    KUBECONFIG,
    NAMESPACE,
    RELEASE,
    ROOT,
    Kube,
    _run,
    helm_as_deployer,
    upgraded,
)

SPEC = "e2e/csp-report.spec.ts"
LOG_WAIT = 30.0  # seconds for the warning to show in the log after the report was delivered
FORWARD = ROOT / "scripts" / "ci" / "kind-forward.sh"

# Run under kind-forward.sh: POSTs to the public forward, one JSON line of the statuses.
PROBE = r"""
import json, os, urllib.error, urllib.parse, urllib.request
base, report, items = os.environ["PUBLIC_URL"], os.environ["REPORT"], os.environ["ITEMS"]
xss = "<script>alert(document.cookie)</script>"
control = f"csp-waf-control-{os.urandom(4).hex()}"
body = json.dumps({"csp-report": {"document-uri": "http://localhost/", "violated-directive": "x"}})
cases = {
    "report_json_xss": (report, "application/json", json.dumps({"csp-report": {"x": xss}})),
    "report_xss_query": (f"{report}?q={urllib.parse.quote(xss)}", "application/csp-report", body),
    "items_report_type": (items, "application/csp-report", body),
    "items_xss_body": (items, "application/json", json.dumps({"name": xss})),
    "report_control": (report, "application/csp-report", body),
    "items_control": (items, "application/json", json.dumps({"name": control})),
}
out = {}
for case, (path, ctype, data) in cases.items():
    req = urllib.request.Request(base + path, data.encode(), {"Content-Type": ctype}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            out[case] = r.status
    except urllib.error.HTTPError as e:
        out[case] = e.code
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def kube() -> Iterator[Kube]:
    """The context guard, then the release with cspReporting on for the module (rolled back)."""
    assert KUBECONFIG.is_file(), f"no {KUBECONFIG}: run make kind-up kind-deploy first"
    guarded = Kube()
    with upgraded("--set", "cspReporting.enabled=true"):
        yield guarded


def test_a_browser_csp_violation_is_logged_by_the_backend(kube: Kube) -> None:
    marker = f"fst{secrets.token_hex(8)}"
    started = time.time()
    cmd = [str(FORWARD), "pnpm", "exec", "playwright", "test", SPEC, "--retries=0"]
    # pnpm's state file (its update check) under .tools, as the Makefile runs pnpm.
    env = {
        **os.environ,
        "CSP_REPORT_MARKER": marker,
        "XDG_STATE_HOME": str(ROOT / ".tools/pnpm/state"),
    }
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


def test_the_waf_exception_covers_only_reports_to_the_report_route(kube: Kube) -> None:  # noqa: ARG001
    """Outside the report route's exception (POST, a report content type, on that path) the WAF
    inspects as everywhere: a JSON body, an XSS query and the report type elsewhere are 403."""
    values = json.loads(
        _run([*helm_as_deployer(), "get", "values", RELEASE, "-n", NAMESPACE, "-a", "-o", "json"])
    )
    env = {**os.environ, "REPORT": f"/api/{values['config']['API_VERSION']}/csp-report"}
    env["ITEMS"] = values["tests"]["apiPath"]  # the chart's sample route
    cmd = [str(FORWARD), sys.executable, "-I", "-c", PROBE]
    done = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    statuses = json.loads(done.stdout.strip().splitlines()[-1])
    print(statuses)
    forbidden = {k: v for k, v in statuses.items() if not k.endswith("_control")}
    assert forbidden == dict.fromkeys(forbidden, 403), statuses
    # The controls pass the WAF, so each 403 above is the WAF's.
    assert statuses["report_control"] == 204, statuses  # noqa: PLR2004
    assert statuses["items_control"] == 201, statuses  # noqa: PLR2004
