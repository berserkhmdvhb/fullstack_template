"""Compose-flavour digest-pin aid: `infra/security/verify-image-digest.sh`.

Network-independent. Asserts the Compose/main flavour states the digest-pin
honestly: `verify:registry` CAPTURES each built image's immutable manifest
digest into `image-digests.env` (`<ID>_IMAGE_DIGEST=sha256:…`), but this
flavour has no helm `deploy:verify` to pin the deploy by it — so the helper is
a verification *aid* for confirming a running image matches the captured
digest, not admission enforcement. The docs must not overclaim that Compose
consumes the digest at deploy time (that is the helm flavour's `deploy.yml` workflow).
"""

import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
HELPER = REPO / "infra/security/verify-image-digest.sh"
SEC = HELPER.parent
# Absolute bash path so ruff S607 (partial executable) is satisfied; the job's
# before_script installs bash, so which() resolves it on CI.
BASH: str = shutil.which("bash") or "/usr/bin/bash"
CAPTURED = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64


def run(args: list[str], env_file: Path) -> subprocess.CompletedProcess[str]:
    # check=False: the harness asserts on the helper's exit code, so non-zero is
    # expected data, not a failure to propagate.
    return subprocess.run(
        [BASH, str(HELPER), *args, "--env", str(env_file)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_helper_exists_and_is_shell() -> None:
    src = HELPER.read_text()
    assert HELPER.is_file()
    assert src.startswith("#!/usr/bin/env bash")
    # A verification *aid*, not an admission gate: it compares the captured digest
    # against a running image's digest as a plain string check — no crypto
    # verification (no cosign) and no deploy-time blocking.
    assert "aid" in src.lower()
    assert "cosign" not in src.lower()


def test_helper_verifies_against_captured_digest(tmp_path: Path) -> None:
    env_file = tmp_path / "image-digests.env"
    env_file.write_text(f"BACKEND_IMAGE_DIGEST={CAPTURED}\n")

    # Matching digest -> success.
    ok = run(
        [
            "--digest",
            "BACKEND_IMAGE_DIGEST",
            "--actual",
            f"registry.example.com/backend@{CAPTURED}",
        ],
        env_file,
    )
    assert ok.returncode == 0, ok.stderr

    # Different digest -> failure with a clear message.
    bad = run(
        ["--digest", "BACKEND_IMAGE_DIGEST", "--actual", f"registry.example.com/backend@{OTHER}"],
        env_file,
    )
    assert bad.returncode != 0
    assert "mismatch" in (bad.stderr + bad.stdout).lower()


def test_helper_errors_when_key_absent(tmp_path: Path) -> None:
    env_file = tmp_path / "image-digests.env"
    env_file.write_text(f"BACKEND_IMAGE_DIGEST={CAPTURED}\n")
    r = run(
        [
            "--digest",
            "FRONTEND_IMAGE_DIGEST",
            "--actual",
            f"registry.example.com/frontend@{CAPTURED}",
        ],
        env_file,
    )
    assert r.returncode != 0
    assert "no captured digest" in (r.stderr + r.stdout).lower()


def test_docs_do_not_overclaim_compose_digest_consumption() -> None:
    # README's Supply-Chain Hardening section must say Compose CAPTURES the
    # digest and that CONSUMING it (deploy-time pin) is the helm flavour — not
    # claim Compose itself pins the deploy by digest.
    readme = (REPO / "README.md").read_text()
    section = readme.split("Supply-Chain Hardening")[-1].split("---")[0]
    if "digest" in section.lower():
        assert "helm" in section.lower() or "deploy:verify" in section.lower(), (
            "README digest-pin section should attribute deploy-time digest "
            "consumption to the helm flavour, not Compose"
        )


# --- house style: shared runtime + CI-clean banner ---------------------------


def test_sources_shared_runtime_and_banner() -> None:
    src = HELPER.read_text()
    assert "_helpers.sh" in src, "verify-image-digest.sh must source the shared runtime"
    assert "lib_banner" in src, "verify-image-digest.sh must open with a banner"


def test_output_is_ci_clean_no_ansi_escapes(tmp_path: Path) -> None:
    # subprocess pipes stdout, so the helper sees a non-TTY: its colour must
    # degrade, leaving the banner + verdict free of raw ANSI escapes in the log.
    env_file = tmp_path / "image-digests.env"
    env_file.write_text(f"BACKEND_IMAGE_DIGEST={CAPTURED}\n")
    r = run(
        [
            "--digest",
            "BACKEND_IMAGE_DIGEST",
            "--actual",
            f"registry.example.com/backend@{CAPTURED}",
        ],
        env_file,
    )
    combined = r.stdout + r.stderr
    assert "\x1b" not in combined, (
        "output must carry no ANSI escapes on CI (colour degrades on non-TTY)"
    )
    assert "┏" in combined, "a framed banner should open the output"
