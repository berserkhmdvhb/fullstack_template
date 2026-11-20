"""Supply-chain tooling: `infra/security/tools-install.sh` pins + verifies tools.

These tests are network-independent. They assert the security properties the
"pin + verify, never curl|bash" rule requires, and that the helper recognises
the tools the supply-chain jobs need (cosign for attestation, trivy for
typosquat) rather than silently accepting anything.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent.parent / "infra/security/tools-install.sh"
SRC = SCRIPT.read_text()
# Absolute bash path so ruff S607 (partial executable) is satisfied; the job's
# before_script installs bash, so which() resolves it on CI.
BASH: str = shutil.which("bash") or "/usr/bin/bash"


def run(args: list[str], dest: Path) -> subprocess.CompletedProcess[str]:
    # check=False: the harness asserts on the script's exit code, so non-zero is
    # expected data, not a failure to propagate.
    return subprocess.run(
        [BASH, str(SCRIPT), *args, str(dest)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_script_exists_and_is_executable_style() -> None:
    assert SCRIPT.is_file()
    assert SCRIPT.read_text().startswith("#!/usr/bin/env bash")


def test_usage_error_when_tool_missing(tmp_path: Path) -> None:
    r = run([], dest=tmp_path)
    assert r.returncode != 0
    assert "usage" in (r.stderr + r.stdout).lower()


def test_unsupported_tool_is_rejected(tmp_path: Path) -> None:
    # Anything outside the allowed set must hit the catch-all, proving cosign /
    # trivy are recognised members of that set (they do not).
    r = run(["not-a-real-tool", "v1.0.0", str(tmp_path)], dest=tmp_path)
    assert r.returncode == 1
    assert "unsupported tool" in r.stderr


def test_installs_checksum_verified_never_pipes_installer_into_shell() -> None:
    # The whole point: download a pinned artifact, verify its SHA-256, then use
    # it. Never pipe a downloaded script straight into a shell interpreter.
    assert "sha256sum -c" in SRC
    # No `curl … | bash` / `| sh` in the CODE: downloads go to a file, then
    # verify. Match against comment-stripped source so the guard inspects actual
    # behaviour, not prose that borrows "|" for "or" (e.g. the "(never curl|bash)"
    # note) — otherwise it false-positives on its own documentation.
    code = "\n".join(ln for ln in SRC.splitlines() if not ln.lstrip().startswith("#"))
    assert not re.search(r"(curl|wget)[^\n|]*\|\s*(bash|sh)\b", code)


def test_recognises_all_supply_chain_tools() -> None:
    # Anchore SBOM/scanners, Sigstore cosign (attestation), Aqua trivy (typosquat).
    assert "github.com/anchore" in SRC
    assert "sigstore/cosign" in SRC
    assert "aquasecurity/trivy" in SRC
    for tool in ("syft", "grype", "grant", "cosign", "trivy"):
        assert tool in SRC


def test_trivy_asset_uses_vendor_casing() -> None:
    # Trivy's release asset is "Linux-64bit" (mixed case); a naive "${OS}-64bit"
    # builds "linux-64bit" and 404s. Pin the exact vendor spelling.
    assert "trivy_${VER}_Linux-64bit.tar.gz" in SRC


# --- house style: shared runtime, errtrace, dry-run --------------------------


def test_runs_with_errtrace_strict_mode() -> None:
    # errtrace (-E) so an error inside a helper reports the failing line, not the
    # caller's — the shared runtime installs the ERR trap this expects.
    assert "set -Eeuo pipefail" in SRC, "tools-install.sh must run under set -Eeuo pipefail"


def test_sources_shared_runtime_and_dry_run() -> None:
    assert "_helpers.sh" in SRC, "tools-install.sh must source the shared runtime"
    assert "DRY_RUN" in SRC, "tools-install.sh must honour DRY_RUN via the shared run helper"


def test_dry_run_performs_no_external_mutations(tmp_path: Path) -> None:
    # Stub the external mutating tools so we can prove they are never *invoked*
    # under --dry-run: the shared runtime's `run` prints the argv (⇢) and
    # executes nothing (a real run would curl the release and install the binary).
    stubdir = tmp_path / "stubs"
    stubdir.mkdir()
    calllog = tmp_path / "calls.log"
    for tool in ("curl", "install", "tar", "chmod", "sha256sum"):
        stub = stubdir / tool
        stub.write_text(f"#!/usr/bin/env bash\necho {tool} >> {calllog}\n")
        stub.chmod(0o755)

    dest = tmp_path / "bin"
    env = dict(os.environ)
    env["PATH"] = f"{stubdir}{os.pathsep}{env['PATH']}"
    env["DRY_RUN"] = "true"
    r = subprocess.run(
        [BASH, str(SCRIPT), "cosign", "v2.4.0", str(dest)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    combined = r.stdout + r.stderr
    assert r.returncode == 0, combined
    assert "⇢" in combined, "dry-run must print the argv (⇢) via the shared run helper"
    called = calllog.read_text() if calllog.exists() else ""
    assert "curl" not in called, f"curl must not run under dry-run; calllog={called!r}"
    assert "install" not in called, f"install must not run under dry-run; calllog={called!r}"
    assert not (dest / "cosign").exists(), "nothing must be installed under dry-run"
