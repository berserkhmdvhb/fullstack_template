"""Every distroless image can take Debian's fix for a package before its base image ships it.

A distroless base is rebuilt from Debian some days after Debian publishes a fix, and until then the
blocking vulnerability gate fails on every image built from it. Each Dockerfile of a distroless
image in .github/container-images.yml has the same three parts: the runtime base declared once by
digest (`runtime-base`), a `debian-fixes` stage on the Debian image the backend builds on, which
runs scripts/docker/debian-fixes.sh on the base's dpkg records and the `DEBIAN_FIXES` list, and a
runtime stage that starts from the base and copies the fixes over it before anything else. The
script's own behaviour is tests/scripts/debian-fixes.bats.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST = yaml.safe_load((ROOT / ".github/container-images.yml").read_text(encoding="utf-8"))
DISTROLESS = [i["dockerfile"] for i in MANIFEST["images"] if i.get("distroless")]
FROM = re.compile(r"^FROM (\S+) AS ([\w-]+)$", re.MULTILINE)
DIGEST = r"@sha256:[0-9a-f]{64}"
# Debian policy: a package name is lower-case alphanumerics and + - . (two or more, starting with an
# alphanumeric); a version starts with a digit. The script applies the same rule.
FIX = re.compile(r"[a-z0-9][a-z0-9+.-]+=[0-9][A-Za-z0-9.+~:-]*")


def _dockerfile(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _stages(text: str) -> dict[str, tuple[str, str]]:
    """Stage name -> (the image it starts FROM, its instructions up to the next FROM)."""
    matches = list(FROM.finditer(text))
    assert len(matches) == len(re.findall(r"^FROM ", text, re.MULTILINE)), (
        "every FROM names its stage"
    )
    ends = [m.start() for m in matches[1:]] + [len(text)]
    return {m[2]: (m[1], text[m.end() : end]) for m, end in zip(matches, ends, strict=True)}


def _instructions(body: str) -> list[str]:
    """A stage's lines without its comments and blank lines."""
    return [ln for ln in body.splitlines() if ln.strip() and not ln.startswith("#")]


def _fixes(text: str) -> list[str]:
    match = re.search(
        r'^ARG DEBIAN_FIXES="([^"]*)"$', _stages(text)["debian-fixes"][1], re.MULTILINE
    )
    assert match, 'the debian-fixes stage declares ARG DEBIAN_FIXES="<package>=<version> ..."'
    return match.group(1).split()


def _backend_debian() -> str:
    match = re.search(
        r"^FROM (debian:13-slim" + DIGEST + ") AS py-builder$",
        _dockerfile("Dockerfile.backend"),
        re.MULTILINE,
    )
    assert match
    return match.group(1)


def test_every_distroless_image_is_covered() -> None:
    assert sorted(DISTROLESS) == ["Dockerfile.backend", "Dockerfile.frontend", "Dockerfile.waf"]


@pytest.mark.parametrize("name", DISTROLESS)
def test_the_runtime_base_is_declared_once_by_digest_and_the_runtime_starts_from_it(
    name: str,
) -> None:
    stages = _stages(_dockerfile(name))
    assert re.search(DIGEST + "$", stages["runtime-base"][0])
    assert stages["runtime"][0] == "runtime-base"
    assert list(stages)[-1] == "runtime"


@pytest.mark.parametrize("name", DISTROLESS)
def test_the_fixes_stage_runs_the_script_on_the_bases_dpkg_records(name: str) -> None:
    image, body = _stages(_dockerfile(name))["debian-fixes"]
    assert image == _backend_debian(), "the Debian image the backend builds on, one digest to move"
    assert "COPY --from=runtime-base /var/lib/dpkg/status.d/ /base-status.d/\n" in body
    assert "COPY scripts/docker/debian-fixes.sh /usr/local/bin/debian-fixes\n" in body
    assert _instructions(body)[-1] == "RUN debian-fixes /base-status.d /out"


@pytest.mark.parametrize("name", DISTROLESS)
def test_the_runtime_copies_the_fixes_over_the_base_before_anything_else(name: str) -> None:
    body = _stages(_dockerfile(name))["runtime"][1]
    assert _instructions(body)[0] == "COPY --from=debian-fixes /out/ /"
    assert body.count("--from=debian-fixes") == 1


@pytest.mark.parametrize("name", DISTROLESS)
def test_each_fix_is_a_package_and_a_version_listed_once(name: str) -> None:
    fixes = _fixes(_dockerfile(name))
    for fix in fixes:
        assert FIX.fullmatch(fix), fix
    packages = [f.split("=", 1)[0] for f in fixes]
    assert len(packages) == len(set(packages))


def test_the_script_ships_in_the_build_context_and_never_in_an_image() -> None:
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert not any(
        p.strip() in {"scripts", "scripts/", "scripts/docker", "scripts/docker/"} for p in ignored
    )
    for name in DISTROLESS:
        assert "debian-fixes.sh" not in _stages(_dockerfile(name))["runtime"][1]
