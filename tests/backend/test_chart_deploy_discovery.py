# your-service-name — adaptable service template (see wiki/getting-started.md)
"""deploy-env.sh's discovery render, against the real chart: it finds exactly the images that run.

values.production.yaml pins every app image to the placeholder digest, so the discovery render shows
them by digest, not by tag. Only `helm template` is real here; the cluster calls are stubbed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.backend.test_chart_production_guard import CHART, ROOT, find_helm, require_helm

SCRIPT = ROOT / "scripts" / "ci" / "deploy-env.sh"
DIGEST = "sha256:" + "a" * 64
STUBS = {
    # helm: the discovery render is the real one; history (no release yet) and upgrade are stubbed.
    "helm": '#!/usr/bin/env bash\necho "helm $*" >> "$LOG"\n'
    'case "$1" in template) exec "$REAL_HELM" "$@" ;;\n'
    '  history) [[ "$*" == *--max* ]]; exit ;; esac\n',
    "kubectl": '#!/usr/bin/env bash\ncase "$*" in *port-forward*) exec sleep 30 ;; esac\n',
    "curl": "#!/usr/bin/env bash\nexit 0\n",
    "matrix": '#!/usr/bin/env bash\nids="${*: -1}"\nout=""\nfor i in ${ids//,/ }; do '
    'out+="{\\"id\\":\\"$i\\",\\"repository\\":\\"ghcr.io/o/r/$i\\"},"; done\n'
    'echo "{\\"include\\":[${out%,}]}"\n',
    "verify": '#!/usr/bin/env bash\necho "verify COMPONENTS=$COMPONENTS" >> "$LOG"\n',
    "smoke": "#!/usr/bin/env bash\nexit 0\n",
}


@pytest.fixture(scope="module")
def helm() -> str:
    return require_helm(find_helm(), ci=bool(os.environ.get("CI")))


def _deploy(helm: str, tmp: Path, *, waf: bool, digests: list[str]) -> tuple[int, str, str]:
    """Run deploy-env.sh for production; return its exit code, output and call log."""
    chart = tmp / "chart"
    shutil.copytree(CHART, chart)
    if waf:
        with (chart / "values.production.yaml").open("a", encoding="utf-8") as f:
            f.write("\nwaf: {enabled: true}\n")
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    for name, body in STUBS.items():
        path = bin_dir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
    log = tmp / "calls.log"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "LOG": str(log),
        "REAL_HELM": helm,
        "MATRIX_CMD": str(bin_dir / "matrix"),
        "VERIFY_SCRIPT": str(bin_dir / "verify"),
        "SMOKE_SCRIPT": str(bin_dir / "smoke"),
        "WORK_DIR": str(tmp),
        "SMOKE_WAIT_TRIES": "1",
        "SMOKE_WAIT_SLEEP": "0",
        "SERVICE_NAME": "your-service-name",
        "DEPLOY_ENV": "production",
        "CHART_DIR": str(chart),
        "HELM_TIMEOUT": "5m",
        "IMAGE_TAG": "t1",
        "DEPLOY_IMAGES": "backend,frontend,waf",
        "DIGESTS": json.dumps({f"{d.upper()}_IMAGE_DIGEST": DIGEST for d in digests}),
        "BACKEND_PORT": "8000",
        "API_VERSION": "v1",
        "GITHUB_STEP_SUMMARY": os.devnull,
    }
    result = subprocess.run(
        ["/bin/bash", str(SCRIPT)], capture_output=True, text=True, env=env, check=False
    )
    return result.returncode, result.stdout + result.stderr, log.read_text(encoding="utf-8")


def test_without_the_waf_discovery_finds_the_backend_and_the_frontend(
    helm: str, tmp_path: Path
) -> None:
    rc, out, log = _deploy(helm, tmp_path, waf=False, digests=["backend", "frontend"])
    assert rc == 0, out
    assert "verify COMPONENTS=backend frontend\n" in log
    assert "waf: not rendered by this release (feature off) and no WAF_IMAGE_DIGEST" in out


def test_with_the_waf_discovery_finds_it_too(helm: str, tmp_path: Path) -> None:
    rc, out, log = _deploy(helm, tmp_path, waf=True, digests=["backend", "frontend", "waf"])
    assert rc == 0, out
    assert "verify COMPONENTS=backend frontend waf\n" in log


def test_with_the_waf_and_no_waf_digest_the_deploy_is_refused(helm: str, tmp_path: Path) -> None:
    rc, out, log = _deploy(helm, tmp_path, waf=True, digests=["backend", "frontend"])
    assert rc == 1
    assert "WAF_IMAGE_DIGEST must be sha256:<64 hex>" in out
    assert "helm upgrade" not in log


def test_the_discovery_render_has_no_guard_and_the_upgrade_has_it(
    helm: str, tmp_path: Path
) -> None:
    rc, out, log = _deploy(helm, tmp_path, waf=False, digests=["backend", "frontend"])
    assert rc == 0, out
    template = next(ln for ln in log.splitlines() if ln.startswith("helm template"))
    upgrade = next(ln for ln in log.splitlines() if ln.startswith("helm upgrade"))
    assert "rejectPlaceholders" not in template
    assert "--set guards.rejectPlaceholders=true" in upgrade
