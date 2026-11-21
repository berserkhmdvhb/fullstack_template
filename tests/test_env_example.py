"""The root .env.example must name every variable the docs, compose file and code read."""

import re
from pathlib import Path

from your_service_name.backend.settings import Settings

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / ".env.example"

# Names deliberately not in .env.example, with the reason. The GITHUB_* names and DEPLOY_ENV in the
# CI/CD table are set by GitHub Actions and the deploy job, never by a developer.
ALLOWED_PREFIXES = {"GITHUB_": "set by GitHub Actions", "DEPLOY_ENV": "set by the deploy job"}


def _names(text: str) -> set[str]:
    """Variable names written as NAME= or # NAME= (set or commented)."""
    return set(re.findall(r"^(?:#\s*)?([A-Z][A-Z0-9_]*)=", text, re.MULTILINE))


def _doc_names() -> set[str]:
    docs = (ROOT / "wiki/environment-variables.md").read_text()
    return set(re.findall(r"^\|\s*`([A-Z][A-Z0-9_]*)`\s*\|", docs, re.MULTILINE))


def _code_names() -> set[str]:
    """Every variable the backend reads: the ``Settings`` fields (the only reader)."""
    return set(Settings.model_fields)


def test_required_variables_are_set_in_env_example() -> None:
    docs = (ROOT / "wiki/environment-variables.md").read_text()
    required = set(re.findall(r"only required value is `([A-Z_]+)`", docs))
    compose = (ROOT / "docker-compose.yml").read_text()
    required |= set(re.findall(r"^\s+\w+: \$\{([A-Z_]+):\?", compose, re.MULTILINE))
    assert "DB_PASSWORD" in required
    uncommented = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", EXAMPLE.read_text(), re.MULTILINE))
    assert required <= uncommented


def test_every_documented_and_read_variable_is_listed() -> None:
    assert _code_names(), "the code scan found nothing; the regexes are broken"
    wanted = {n for n in _doc_names() | _code_names() if not n.startswith(tuple(ALLOWED_PREFIXES))}
    missing = wanted - _names(EXAMPLE.read_text())
    assert not missing, f"missing from .env.example: {sorted(missing)}"


def test_uncommented_secret_values_are_placeholders() -> None:
    for name, value in re.findall(r"^([A-Z][A-Z0-9_]*)=(.*)$", EXAMPLE.read_text(), re.MULTILINE):
        if re.search(r"PASSWORD|SECRET|TOKEN|KEY", name):
            assert value in ("", "change-me"), name
