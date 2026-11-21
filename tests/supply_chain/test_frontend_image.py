"""Dockerfile.frontend pins node by digest, and the builder's node is the pinned tooling node."""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
DOCKERFILE = (ROOT / "Dockerfile.frontend").read_text(encoding="utf-8")
FROM = re.compile(r"^FROM (\S+) AS (\w+)$", re.MULTILINE)


def _stage(name: str) -> str:
    return next(m[1] for m in FROM.finditer(DOCKERFILE) if m[2] == name)


def _pinned_node_version() -> str:
    rows = (ROOT / "tools/pins.tsv").read_text(encoding="utf-8").splitlines()
    return next(r.split("\t")[1] for r in rows if r.startswith("node\t"))


def test_the_builder_node_is_the_pinned_node_version() -> None:
    match = re.fullmatch(r"node:([\d.]+)-trixie-slim@sha256:[0-9a-f]{64}", _stage("builder"))
    assert match, "the builder must be node:<version>-trixie-slim pinned by digest"
    assert match.group(1) == _pinned_node_version()


def test_the_build_passes_the_image_version_to_next_as_the_deployment_id() -> None:
    """Next.js accepts letters, digits, - and _ only, so the version is sanitised on the way in."""
    builder = DOCKERFILE.split("AS runtime")[0]
    assert re.search(r"^ARG VERSION$", builder, re.MULTILINE), "the builder needs the VERSION arg"
    assert re.search(
        r"NEXT_DEPLOYMENT_ID=\"\$\(.*VERSION.*tr -c 'A-Za-z0-9_-' '_'.*\)\" npx next build$",
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


def test_package_manager_is_the_npm_bundled_with_the_pinned_node() -> None:
    pin = _pinned_node_version()
    meta = ROOT / f".tools/opt/node-{pin}/node-v{pin}-linux-x64/lib/node_modules/npm/package.json"
    if not meta.is_file():
        pytest.skip("the pinned Node is not installed in .tools (make tools)")
    bundled = json.loads(meta.read_text(encoding="utf-8"))["version"]
    package = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    assert package["packageManager"] == f"npm@{bundled}"


def test_the_runtime_is_the_pinned_nodejs_major_pinned_by_digest() -> None:
    major = _pinned_node_version().split(".")[0]
    assert re.fullmatch(
        rf"gcr\.io/distroless/nodejs{major}-debian\d+@sha256:[0-9a-f]{{64}}", _stage("runtime")
    )


def test_every_npm_runner_checks_the_package_manager_version() -> None:
    """CI and both image builds run check-npm-version.mjs; it needs no Node here, only the calls."""
    action = (ROOT / ".github/actions/setup-frontend/action.yml").read_text(encoding="utf-8")
    assert "node scripts/check-npm-version.mjs" in action
    assert action.index("node scripts/check-npm-version.mjs") < action.index("npm ci")
    for name in ("Dockerfile.frontend",):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "COPY frontend/scripts/check-npm-version.mjs" in text, name
        assert text.index("node scripts/check-npm-version.mjs") < text.index("&& npm ci"), name
    package = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    assert re.fullmatch(r"npm@\d+\.\d+\.\d+", package["packageManager"])


def test_every_npm_runner_ignores_dependency_install_scripts() -> None:
    """The install is `npm ci --ignore-scripts` everywhere; `allowScripts` records the policy."""
    action = (ROOT / ".github/actions/setup-frontend/action.yml").read_text(encoding="utf-8")
    assert re.search(r"^\s+run: npm ci --ignore-scripts$", action, re.MULTILINE)
    for name in ("Dockerfile.frontend",):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "&& npm ci --include=dev --ignore-scripts\n" in text, name


def test_the_healthcheck_probes_the_frontends_own_health_route() -> None:
    health = re.search(r"^HEALTHCHECK [^\n]*\\\n[^\n]*", DOCKERFILE, re.MULTILINE)
    assert health
    assert "/api/healthz`" in health.group(0)


def test_the_image_binds_every_interface() -> None:
    """Next's standalone server.js listens on $HOSTNAME, which docker sets to the container id."""
    runtime = DOCKERFILE[DOCKERFILE.index(" AS runtime") :]
    assert re.search(r"^ENV HOSTNAME=0\.0\.0\.0$", runtime, re.MULTILINE)


def test_the_compose_healthcheck_matches_the_images() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "${FRONTEND_PORT:-3000}/api/healthz`" in compose
