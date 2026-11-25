"""Dockerfile.frontend pins node by digest, the builder's node is the pinned tooling node, and its
pnpm is the pinned pnpm, checked against the same sha256 as mise.lock."""

import json
import re
from pathlib import Path

from tests.supply_chain._pins import lock_entry
from tests.supply_chain._pins import pin as pinned

ROOT = Path(__file__).resolve().parent.parent.parent
DOCKERFILE = (ROOT / "Dockerfile.frontend").read_text(encoding="utf-8")
FROM = re.compile(r"^FROM (\S+) AS ([\w-]+)$", re.MULTILINE)


def _stage(name: str) -> str:
    """The image a stage starts FROM."""
    return next(m[1] for m in FROM.finditer(DOCKERFILE) if m[2] == name)


def _stage_text(name: str) -> str:
    """A stage's instructions, from its FROM line to the next FROM."""
    starts = [(m.start(), m[2]) for m in FROM.finditer(DOCKERFILE)]
    ends = [s for s, _ in starts[1:]] + [len(DOCKERFILE)]
    return next(DOCKERFILE[s:e] for (s, n), e in zip(starts, ends, strict=True) if n == name)


def _pinned_node_version() -> str:
    return pinned("node")


def test_the_builder_node_is_the_pinned_node_version() -> None:
    match = re.fullmatch(r"node:([\d.]+)-trixie-slim@sha256:[0-9a-f]{64}", _stage("builder"))
    assert match, "the builder must be node:<version>-trixie-slim pinned by digest"
    assert match.group(1) == _pinned_node_version()


def test_the_build_derives_the_deployment_id_from_the_version_and_the_commit() -> None:
    """One id per build: the version alone repeats on every main build."""
    builder = _stage_text("builder")
    assert re.search(r"^ARG VERSION$", builder, re.MULTILINE)
    assert re.search(r"^ARG VCS_REF$", builder, re.MULTILINE)
    assert re.search(
        r"NEXT_DEPLOYMENT_ID=\"\$\(node scripts/deployment-id\.mjs"
        r" \"\$\{VERSION\}\" \"\$\{VCS_REF\}\"\)\" pnpm exec next build$",
        builder,
        re.MULTILINE,
    )


def test_the_version_files_and_engines_follow_the_pin() -> None:
    pin = _pinned_node_version()
    for name in (".nvmrc", ".node-version"):
        assert (ROOT / "frontend" / name).read_text(encoding="utf-8").strip() == pin
    engines = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))["engines"]
    major = int(pin.split(".")[0])
    assert engines["node"] == f">={major} <{major + 1}"


def test_package_manager_is_the_pinned_pnpm() -> None:
    for project in ("frontend", "docs-site"):
        package = json.loads((ROOT / project / "package.json").read_text(encoding="utf-8"))
        assert package["packageManager"] == f"pnpm@{pinned('pnpm')}", project


def _arg(name: str) -> str:
    match = re.search(rf"^ARG {name}=(\S+)$", DOCKERFILE, re.MULTILINE)
    assert match, name
    return match[1]


def test_the_image_pnpm_is_the_pinned_release_with_the_locked_checksum() -> None:
    """One source: the version is mise.toml's and the amd64 sha256 is mise.lock's linux-x64 entry.
    mise.lock holds no linux-arm64, so the arm64 sha256 is the release's asset digest, which
    `make pins-refresh` checks."""
    assert _arg("PNPM_VERSION") == pinned("pnpm")
    locked = lock_entry("pnpm", "linux-x64")
    assert f"sha256:{_arg('PNPM_SHA256_AMD64')}" == locked["checksum"]
    assert locked["url"] == (
        f"https://github.com/pnpm/pnpm/releases/download/v{pinned('pnpm')}/pnpm-linux-x64.tar.gz"
    )
    assert re.fullmatch(r"[0-9a-f]{64}", _arg("PNPM_SHA256_ARM64"))
    url = (
        "https://github.com/pnpm/pnpm/releases/download/v${PNPM_VERSION}/pnpm-linux-ASSET.tar.gz"
        " /pnpm.tar.gz"
    )
    for arch, asset in (("amd64", "x64"), ("arm64", "arm64")):
        stage = _stage_text(f"pnpm-{arch}")
        assert _stage(f"pnpm-{arch}") == "scratch"
        assert (
            f"ADD --checksum=sha256:${{PNPM_SHA256_{arch.upper()}}} {url.replace('ASSET', asset)}"
            in stage
        ), arch
    assert _stage("pnpm") == "pnpm-${TARGETARCH}"
    assert "COPY --from=pnpm /pnpm.tar.gz" in _stage_text("builder")


def test_the_runtime_is_the_pinned_nodejs_major_pinned_by_digest() -> None:
    major = _pinned_node_version().split(".")[0]
    assert re.fullmatch(
        rf"gcr\.io/distroless/nodejs{major}-debian\d+@sha256:[0-9a-f]{{64}}", _stage("runtime-base")
    )
    assert _stage("runtime") == "runtime-base"
    assert len(re.findall(r"^FROM gcr\.io/distroless/", DOCKERFILE, re.MULTILINE)) == 1


def test_every_pnpm_runner_checks_the_package_manager_version() -> None:
    """CI and the image build run check-pnpm-version.mjs before they install (the calls only)."""
    action = (ROOT / ".github/actions/setup-frontend/action.yml").read_text(encoding="utf-8")
    assert "node scripts/check-pnpm-version.mjs" in action
    assert action.index("node scripts/check-pnpm-version.mjs") < action.index("pnpm install")
    builder = _stage_text("builder")
    assert "COPY frontend/scripts/check-pnpm-version.mjs" in builder
    assert builder.index("node scripts/check-pnpm-version.mjs") < builder.index("&& pnpm install")


def test_every_pnpm_runner_installs_the_lockfile_as_it_is() -> None:
    """`pnpm install --frozen-lockfile` everywhere, with pnpm-workspace.yaml's settings (no
    `--ignore-scripts`:
    it would also turn the unreviewed-build check off)."""
    action = (ROOT / ".github/actions/setup-frontend/action.yml").read_text(encoding="utf-8")
    assert re.search(r"^\s+run: pnpm install --frozen-lockfile$", action, re.MULTILINE)
    builder = _stage_text("builder")
    assert "&& pnpm install --frozen-lockfile\n" in builder
    assert (
        "COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./"
        in builder
    )
    for path in [ROOT / "Dockerfile.frontend", *sorted((ROOT / ".github").rglob("*.yml"))]:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\bnpm (ci|install|run|test)\b|\bnpx\b", text), path.name
        for line in re.findall(r"pnpm install[^\n]*", text):
            assert line.strip().rstrip("\\").strip() == "pnpm install --frozen-lockfile", (
                path.name,
                line,
            )


def test_the_healthcheck_probes_the_frontends_own_health_route() -> None:
    health = re.search(r"^HEALTHCHECK [^\n]*\\\n[^\n]*", DOCKERFILE, re.MULTILINE)
    assert health
    assert "/api/healthz`" in health.group(0)


def test_the_image_binds_every_interface() -> None:
    """Next's standalone server.js listens on $HOSTNAME, which docker sets to the container id."""
    runtime = _stage_text("runtime")
    assert re.search(r"^ENV HOSTNAME=0\.0\.0\.0$", runtime, re.MULTILINE)


def test_the_compose_healthcheck_matches_the_images() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "${FRONTEND_PORT:-3000}/api/healthz`" in compose
