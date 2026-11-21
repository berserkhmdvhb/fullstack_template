"""Dockerfile.backend builds from uv.lock with the pinned uv and a root-owned venv."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
DOCKERFILE = (ROOT / "Dockerfile.backend").read_text(encoding="utf-8")
UV_COPY = re.compile(
    r"^COPY --from=ghcr\.io/astral-sh/uv:(\S+)@sha256:([0-9a-f]{64}) /uv /bin/uv$", re.MULTILINE
)


def _pinned_uv_version() -> str:
    rows = (ROOT / "tools/pins.tsv").read_text(encoding="utf-8").splitlines()
    return next(r.split("\t")[1] for r in rows if r.startswith("uv\t"))


def test_the_uv_image_tag_is_the_pinned_uv_version_and_a_digest() -> None:
    match = UV_COPY.search(DOCKERFILE)
    assert match, "the builder must copy uv from the official image, pinned by tag and digest"
    assert match.group(1) == _pinned_uv_version()


def test_the_builder_installs_the_locked_runtime_set_in_two_layers() -> None:
    frozen = [ln for ln in DOCKERFILE.splitlines() if "uv sync" in ln]
    deps, project = frozen  # exactly two syncs
    for ln in frozen:
        assert all(flag in ln.split() for flag in ("--frozen", "--no-dev", "--no-editable"))
    assert "--no-install-project" in deps
    assert "--no-install-project" not in project
    assert "pip install" not in DOCKERFILE.split("AS runtime")[0]
    for setting in ("UV_COMPILE_BYTECODE=1", "UV_LINK_MODE=copy", "UV_PYTHON_DOWNLOADS=never"):
        assert setting in DOCKERFILE


def test_the_venv_is_copied_root_owned_and_first_on_path() -> None:
    copy = next(ln for ln in DOCKERFILE.splitlines() if "/app/.venv /app/.venv" in ln)
    assert copy.startswith("COPY --from=py-builder ")
    assert "--chown" not in copy
    path = next(ln for ln in DOCKERFILE.splitlines() if ln.startswith("ENV PATH="))
    assert path.startswith('ENV PATH="/app/.venv/bin:')
    chowns = [ln for ln in DOCKERFILE.splitlines() if "chown -R" in ln]
    assert not any(re.search(r"chown -R \S+ /app\b(?!/)", ln) for ln in chowns)


def test_the_image_keeps_its_other_hardening() -> None:
    assert "USER 10001" in DOCKERFILE
    assert "HEALTHCHECK" in DOCKERFILE
    assert "pip>=25.3" in DOCKERFILE.split("AS runtime")[1]


def test_code_and_config_copies_are_root_owned_and_only_the_writable_dirs_are_chowned() -> None:
    lines = DOCKERFILE.split("AS runtime")[1].splitlines()
    for ln in lines:
        if ln.startswith("COPY") and not ln.startswith("COPY --from"):
            assert "--chown" not in ln, ln
    chown = next(ln for ln in lines if "chown " in ln and "useradd" not in ln)
    assert chown.split("chown ")[1].split() == ["appuser:appuser", "/app/logs", "/app/data"]


def _runtime() -> list[str]:
    return DOCKERFILE.split("AS runtime")[1].splitlines()


def _instruction(name: str) -> str:
    """One Dockerfile instruction of the runtime stage, continuation lines joined."""
    text = "\n".join(_runtime()).replace("\\\n", " ")
    return next(ln for ln in text.splitlines() if ln.startswith(name))


def test_the_healthcheck_is_the_python_probe_in_exec_form() -> None:
    check = _instruction("HEALTHCHECK")
    assert '["/app/.venv/bin/python", "-m", "your_service_name.backend.healthcheck"]' in check
    assert "curl" not in check
    assert "--interval=10s --timeout=5s --start-period=30s --retries=5" in check


def test_the_command_is_the_python_launcher() -> None:
    assert _instruction("CMD") == (
        'CMD ["/app/.venv/bin/python", "-m", "your_service_name.backend.serve"]'
    )


def test_the_runtime_stage_has_no_curl_and_no_bash() -> None:
    code = [ln.split("#")[0] for ln in _runtime()]
    assert not [ln for ln in code if re.search(r"\b(curl|bash)\b", ln)]


def test_no_shell_script_is_copied_into_the_runtime_stage() -> None:
    copies = [ln for ln in _runtime() if ln.startswith("COPY")]
    assert not [ln for ln in copies if ".sh" in ln]


def test_the_compose_healthcheck_runs_the_same_probe_as_the_image() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    test = compose["services"]["backend"]["healthcheck"]["test"]
    assert test == [
        "CMD",
        "/app/.venv/bin/python",
        "-m",
        "your_service_name.backend.healthcheck",
    ]
