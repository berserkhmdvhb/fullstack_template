"""The root .dockerignore keeps host-only files out of every image build, and nothing a COPY needs.

The match below is an explicit approximation of Docker's rules (documented, not a re-implementation
of every corner): patterns are anchored at the context root, `**/` matches any number of leading
directories, `*` and `?` stay inside one path segment, a pattern that matches a directory excludes
everything under it, a later `!pattern` re-includes, and the last matching pattern wins.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
IGNORE = REPO / ".dockerignore"
DOCKERFILES = ("Dockerfile.backend", "Dockerfile.frontend", "Dockerfile.nginx")

# Patterns every image build must have (a missing one lets a host file into the build context).
REQUIRED = {
    ".git",
    ".venv",
    ".tools",
    "**/node_modules",
    "frontend/.next",
    "output/",
    "**/__pycache__",
    "**/*.pyc",
    ".env",
    "**/.env",
    "**/.env.*",
}
# Paths that must never reach a build, whatever the exact patterns are.
MUST_EXCLUDE = [
    ".git/config",
    ".venv/bin/python",
    ".tools/bin/helm",
    "frontend/node_modules/planted-marker/index.js",
    "node_modules/x",
    "frontend/.next/cache/x",
    "output/report.json",
    "src/pkg/__pycache__/a.cpython-313.pyc",
    "src/pkg/a.pyc",
    "coverage.xml",
    "test-results/junit.xml",
    ".env",
    "frontend/.env",
    "frontend/.env.local",
    "frontend/.env.production",
    "frontend/.env.example",
    "infra/x/.env.test",
]


def _patterns() -> list[str]:
    lines = (line.strip() for line in IGNORE.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def _regex(pattern: str) -> re.Pattern[str]:
    out, i = "", 0
    pattern = pattern.rstrip("/")
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + "$")


def excluded(path: str, patterns: list[str]) -> bool:
    """Is ``path`` (relative to the context root) excluded?"""
    parts = path.strip("/").split("/")
    verdict = False
    for pattern in patterns:
        negate = pattern.startswith("!")
        regex = _regex(pattern.lstrip("!"))
        # The path itself, or any parent directory of it, matching is enough.
        if any(regex.match("/".join(parts[: n + 1])) for n in range(len(parts))):
            verdict = not negate
    return verdict


def copy_sources(dockerfile: str) -> list[str]:
    """Every build-context COPY/ADD source (``--from=`` copies are not from the context)."""
    sources: list[str] = []
    for line in (REPO / dockerfile).read_text(encoding="utf-8").splitlines():
        words = line.split()
        if not words or words[0] not in {"COPY", "ADD"}:
            continue
        args = [w for w in words[1:] if not w.startswith("--")]
        if any(w.startswith("--from=") for w in words[1:]):
            continue
        sources += args[:-1]
    return sources


def test_the_dockerignore_exists_and_has_every_required_pattern() -> None:
    assert IGNORE.is_file(), "a root .dockerignore is required (the Dockerfiles build from `.`)"
    missing = REQUIRED - set(_patterns())
    assert not missing, f"missing from .dockerignore: {sorted(missing)}"


@pytest.mark.parametrize("path", MUST_EXCLUDE)
def test_host_only_files_are_excluded(path: str) -> None:
    assert excluded(path, _patterns()), path


def test_no_env_file_is_re_included() -> None:
    assert not [p for p in _patterns() if p.startswith("!")], "no pattern may re-include a file"


@pytest.mark.parametrize("dockerfile", DOCKERFILES)
def test_nothing_a_dockerfile_copies_is_excluded(dockerfile: str) -> None:
    sources = copy_sources(dockerfile)
    assert sources, f"no COPY sources found in {dockerfile}; the parser is stale"
    for source in sources:
        assert (REPO / source).exists(), f"{dockerfile} copies {source}, which does not exist"
        assert not excluded(source, _patterns()), f"{dockerfile} copies {source}, which is excluded"


def test_the_frontend_build_inputs_survive() -> None:
    for path in (
        "frontend/package.json",
        "frontend/package-lock.json",
        "frontend/app",
        "frontend/public",
    ):
        assert not excluded(path, _patterns()), path


def test_the_matcher_follows_the_documented_rules() -> None:
    assert excluded("a/b/node_modules/c", ["**/node_modules"])
    assert not excluded("a/b/modules/c", ["**/node_modules"])
    assert not excluded("a/node_modules", ["node_modules"]), "anchored at the context root"
    assert excluded("x.pyc", ["**/*.pyc"])
    assert not excluded("x.env", [".env"])
    assert not excluded("frontend/.env.local", ["**/.env.*", "!frontend/.env.local"])
