"""Dockerfile.backend builds from uv.lock with the pinned uv and a root-owned venv."""

import re
from pathlib import Path

import yaml

from tests.supply_chain._pins import pin

ROOT = Path(__file__).resolve().parent.parent.parent
DOCKERFILE = (ROOT / "Dockerfile.backend").read_text(encoding="utf-8")
UV_COPY = re.compile(
    r"^COPY --from=ghcr\.io/astral-sh/uv:(\S+)@sha256:([0-9a-f]{64}) /uv /bin/uv$", re.MULTILINE
)


def _pinned_uv_version() -> str:
    return pin("uv")


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
    assert "pip install" not in _stage_text("py-builder")
    for setting in (
        "UV_COMPILE_BYTECODE=1",
        "UV_LINK_MODE=copy",
        "UV_PYTHON_DOWNLOADS=never",
        "UV_PYTHON=/usr/bin/python3.13",
        "UV_PYTHON_PREFERENCE=only-system",
    ):
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


def test_there_is_no_pip_at_all_which_is_stronger_than_a_pinned_pip() -> None:
    # The distroless runtime ships no pip, so the old "pip>=25.3" pin is not needed any more.
    assert not re.search(r"\bpip\b", re.sub(r"\bpip-audit\b", "", _stage_text("runtime")))


def test_code_and_config_copies_are_root_owned_and_only_the_writable_dirs_are_chowned() -> None:
    owned = [ln for ln in _runtime() if ln.startswith("COPY") and "--chown" in ln]
    assert sorted(ln.split()[-1] for ln in owned) == ["/app/data", "/home/appuser"]
    for ln in owned:
        assert "--chown=10001:10001" in ln.split(), ln
    assert not [ln for ln in _runtime() if ln.startswith("RUN") or "chown " in ln]


def _runtime() -> list[str]:
    return _stage_text("runtime").splitlines()


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


FROM = re.compile(r"^FROM (\S+)(?: AS (\S+))?$", re.MULTILINE)


def _froms() -> list[tuple[str, str | None]]:
    return [(m[1], m[2]) for m in FROM.finditer(DOCKERFILE)]


def _stage_text(name: str) -> str:
    parts = re.split(r"^FROM .*$", DOCKERFILE, flags=re.MULTILINE)
    idx = [n for _, n in _froms()].index(name)
    return parts[idx + 1]


def test_the_runtime_base_is_declared_once_as_distroless_python_by_digest() -> None:
    froms = {n: i for i, n in _froms()}
    assert re.fullmatch(
        r"gcr\.io/distroless/python3-debian13@sha256:[0-9a-f]{64}", froms["runtime-base"]
    )
    assert DOCKERFILE.count("distroless/python3-debian13") == 1
    assert froms["runtime"] == "runtime-base"
    assert "debug" not in froms["runtime-base"]
    assert "nonroot" not in froms["runtime-base"]


def test_the_builder_is_debian_13_and_the_runtime_names_the_same_major() -> None:
    froms = {n: i for i, n in _froms()}
    builder = re.fullmatch(r"debian:(\d+)-slim@sha256:[0-9a-f]{64}", froms["py-builder"])
    assert builder, "the builder must be debian:<major>-slim pinned by digest"
    runtime = re.search(r"python3-debian(\d+)@", froms["runtime-base"])
    assert runtime
    assert builder.group(1) == runtime.group(1) == "13"


def test_every_image_the_dockerfile_pulls_is_pinned_by_digest() -> None:
    for image, _name in _froms():
        assert re.search(r"@sha256:[0-9a-f]{64}$", image) or image == "runtime-base", image
    copies = re.findall(r"^COPY --from=(\S+)", DOCKERFILE, re.MULTILINE)
    stages = {n for _, n in _froms() if n}
    for ref in copies:
        assert ref in stages or re.search(r"@sha256:[0-9a-f]{64}$", ref), ref


def test_the_runtime_stage_has_no_run_and_no_shell_instruction() -> None:
    text = _stage_text("runtime")
    assert not re.search(r"^(RUN|SHELL)\b", text, re.MULTILINE)
    for name in ("ENTRYPOINT", "CMD", "HEALTHCHECK"):
        assert re.search(rf'{name}(?: .*)? \["', text.replace("\\\n", " ")), name


def test_the_runtime_files_come_from_the_runtime_base_stage_not_a_second_digest() -> None:
    assert "/etc/passwd" in _stage_text("py-builder")
    assert "--from=runtime-base" in _stage_text("py-builder")
    assert "gcr.io" not in _stage_text("py-builder")


def test_tini_is_the_debian_package_binary_with_its_dpkg_record_and_copyright() -> None:
    runtime = _stage_text("runtime")
    assert "COPY --from=py-builder /usr/bin/tini /usr/bin/tini" in runtime
    assert re.search(
        r"COPY --from=py-builder \S+ /var/lib/dpkg/status\.d/tini$", runtime, re.MULTILINE
    )
    assert "/usr/share/doc/tini/copyright" in runtime
    assert "dpkg-query -s tini" in _stage_text("py-builder")


def test_the_user_has_a_passwd_and_group_entry_and_a_home() -> None:
    builder = _stage_text("py-builder")
    assert "appuser:x:10001:10001::/home/appuser:/sbin/nologin" in builder
    assert "appuser:x:10001:" in builder
    assert "/home/appuser" in _stage_text("runtime")
    assert re.search(r"^USER 10001$", _stage_text("runtime"), re.MULTILINE)


def test_the_runtime_drops_what_only_the_slim_base_needed() -> None:
    runtime = _stage_text("runtime")
    assert "DEBIAN_FRONTEND" not in runtime
    assert "apt-get" not in runtime


def test_the_builder_installs_no_libpq_headers() -> None:
    assert "libpq" not in DOCKERFILE


def test_the_source_label_comes_from_a_build_argument() -> None:
    assert re.search(r"^ARG SOURCE_URL=\S+$", DOCKERFILE, re.MULTILINE)
    assert 'org.opencontainers.image.source="${SOURCE_URL}"' in _stage_text("runtime")


def test_no_log_directory_is_created_logs_go_to_stdout() -> None:
    assert "/logs" not in DOCKERFILE
