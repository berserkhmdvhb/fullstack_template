"""Tests for ``scripts/gen_env_docs.py``: the Settings reference in the env-var doc cannot drift."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from your_service_name.backend.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "gen_env_docs.py"
DOC = ROOT / "wiki" / "environment-variables.md"
ENV_EXAMPLE = ROOT / ".env.example"
BACKEND_DEPLOYMENT = ROOT / "charts" / "your-service-name" / "templates" / "backend-deployment.yaml"
FAKE_SECRET = "fake-secret-value-not-real"  # noqa: S105


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_env_docs", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["gen_env_docs"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gen() -> ModuleType:
    return _load()


def _run(*args: str, doc: Path = DOC) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--doc", str(doc), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )


class TestRender:
    def test_every_field_has_a_row_with_its_columns(self, gen: ModuleType) -> None:
        block = gen.render(Settings)
        for name, field in Settings.model_fields.items():
            row = next(line for line in block.splitlines() if line.startswith(f"| `{name}` |"))
            assert (field.description or "") in row

    def test_secrets_are_shown_as_secret_never_as_a_value(self, gen: ModuleType) -> None:
        block = gen.render(Settings)
        for name in ("AUTH_JWT_SECRET", "DATABASE_URL", "TEST_DATABASE_URL"):
            row = next(line for line in block.splitlines() if line.startswith(f"| `{name}` |"))
            assert "(secret)" in row

    def test_production_rules_are_listed(self, gen: ModuleType) -> None:
        block = gen.render(Settings)
        row = next(line for line in block.splitlines() if line.startswith("| `AUTH_MODE` |"))
        assert "ALLOW_ANONYMOUS" in row

    def test_a_header_explains_env(self, gen: ModuleType) -> None:
        assert "`production`" in gen.render(Settings).split("| Variable")[0]

    def test_output_does_not_depend_on_the_environment(
        self, gen: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        before = gen.render(Settings)
        monkeypatch.setenv("AUTH_JWT_SECRET", FAKE_SECRET)
        monkeypatch.setenv("AUTH_MODE", "gateway")
        monkeypatch.setenv("ENV", "production")
        after = gen.render(Settings)
        assert before == after
        assert FAKE_SECRET not in after


class TestDriftCheck:
    def test_the_committed_doc_is_current(self) -> None:
        result = _run("--check")
        assert result.returncode == 0, result.stdout + result.stderr

    def test_a_stale_block_fails_and_says_how_to_fix_it(self, tmp_path: Path) -> None:
        stale = tmp_path / "doc.md"
        stale.write_text(
            DOC.read_text(encoding="utf-8").replace("`LOG_LEVEL` |", "`LOG_LEVELX` |", 1)
        )
        result = _run("--check", doc=stale)
        assert result.returncode == 1
        assert "make env-docs" in result.stderr

    def test_write_repairs_the_block_and_keeps_the_hand_written_text(self, tmp_path: Path) -> None:
        text = DOC.read_text(encoding="utf-8")
        broken = tmp_path / "doc.md"
        broken.write_text(text.replace("`LOG_LEVEL` |", "`LOG_LEVELX` |", 1))
        assert _run(doc=broken).returncode == 0
        assert broken.read_text(encoding="utf-8") == text
        assert _run("--check", doc=broken).returncode == 0

    def test_a_doc_without_markers_is_an_error(self, tmp_path: Path) -> None:
        bare = tmp_path / "doc.md"
        bare.write_text("# no markers\n")
        assert _run("--check", doc=bare).returncode == 2  # noqa: PLR2004


class TestConsistency:
    """One source of truth: the names in .env.example and the chart are all real."""

    def test_every_settings_field_is_in_the_doc_and_in_env_example(self) -> None:
        doc = DOC.read_text(encoding="utf-8")
        example = ENV_EXAMPLE.read_text(encoding="utf-8")
        for name in Settings.model_fields:
            assert f"| `{name}` |" in doc, name
            assert re.search(rf"^(?:#\s*)?{name}=", example, re.MULTILINE), name

    def test_env_example_lists_only_names_the_docs_define(self) -> None:
        """A typo or a leftover name in .env.example matches neither Settings nor the doc."""
        doc = DOC.read_text(encoding="utf-8")
        doc_names = set(re.findall(r"^\|\s*`([A-Z][A-Z0-9_]*)`\s*\|", doc, re.MULTILINE))
        example = ENV_EXAMPLE.read_text(encoding="utf-8")
        listed = set(re.findall(r"^(?:#\s*)?([A-Z][A-Z0-9_]*)=", example, re.MULTILINE))
        assert listed - doc_names == set()

    def test_backend_env_vars_named_in_the_chart_are_settings_fields(self) -> None:
        """The `env:` entries of the backend container (the rest comes from the ConfigMap)."""
        text = BACKEND_DEPLOYMENT.read_text(encoding="utf-8")
        match = re.search(r"^\s+env:\n(.*?)^\s+ports:", text, re.MULTILINE | re.DOTALL)
        assert match, "the backend container has no env: block; the pattern is stale"
        names = set(re.findall(r"- name: ([A-Z][A-Z0-9_]*)", match.group(1)))
        assert "DATABASE_URL" in names
        assert names <= set(Settings.model_fields)
