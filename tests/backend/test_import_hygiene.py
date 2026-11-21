# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Importing settings or the migration runner must not build the app; Settings reads ``.env``."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from your_service_name.backend.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
# An environment that is invalid for the app but must not matter to a bare import.
BAD_PRODUCTION = {"ENV": "production", "DATABASE_URL": "sqlite:///x.db", "AUTH_MODE": "ldap"}


def _clean_env(extra: dict[str, str]) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in Settings.model_fields}
    return {**env, "PYTHONPATH": str(SRC), **extra}


def _import(code: str, extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env=_clean_env(extra),
    )


@pytest.mark.parametrize(
    "module",
    [
        "your_service_name.backend.settings",
        "your_service_name.backend.db.run_migrations",
        "your_service_name.backend.db.engine",
        "your_service_name.backend",
    ],
)
def test_importing_does_not_build_the_app(module: str) -> None:
    code = f"import sys, {module}; assert 'your_service_name.backend.main' not in sys.modules"
    result = _import(code, BAD_PRODUCTION)
    assert result.returncode == 0, result.stderr
    assert "Rate limiting enabled" not in result.stderr


def test_importing_main_still_provides_the_asgi_app() -> None:
    result = _import(
        "from your_service_name.backend.main import app; print(app.title)", {"API_TITLE": "T"}
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "T"


def test_importing_main_under_a_bad_environment_fails_loudly() -> None:
    result = _import("import your_service_name.backend.main", BAD_PRODUCTION)
    assert result.returncode != 0
    assert "AUTH_MODE" in result.stderr


@pytest.mark.parametrize("extra", [{}, {"ENV": "production"}])
def test_env_docs_check_passes_whatever_the_environment(extra: dict[str, str]) -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_env_docs.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env=_clean_env({**BAD_PRODUCTION, **extra}),
    )
    assert result.returncode == 0, result.stderr


class TestDotenv:
    """``.env`` in the working directory is a Settings source; real variables win."""

    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        for name in Settings.model_fields:
            monkeypatch.delenv(name, raising=False)
        (tmp_path / ".env").write_text("API_TITLE=From Dotenv\nBACKEND_PORT=1\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

    def test_a_dotenv_in_the_working_directory_is_read(self) -> None:
        assert Settings().API_TITLE == "From Dotenv"

    def test_a_real_environment_variable_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("API_TITLE", "From Env")
        assert Settings().API_TITLE == "From Env"

    def test_unrelated_names_in_dotenv_are_ignored(self) -> None:
        Settings()  # BACKEND_PORT=1 is a shell variable, not a field


def test_no_backend_image_can_contain_a_dotenv() -> None:
    """The backend and nginx images copy named paths only; no Dockerfile copies the repo root."""
    for name in ("Dockerfile.backend", "Dockerfile.nginx"):
        for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
            match = re.match(r"\s*(?:COPY|ADD)\s+(?:--\S+\s+)*(.+?)\s+\S+\s*$", line)
            if not match or "--from=" in line:
                continue
            for source in match.group(1).split():
                assert source not in {".", "./", "*"}, f"{name}: {line}"
                assert not Path(source).name.startswith(".env"), f"{name}: {line}"


def test_features_off_import_no_tracing_sdk_or_redis() -> None:
    """The opt-in dependencies are imported only when their feature is on.

    FastAPI itself imports the small ``opentelemetry`` API package (its own, inert telemetry), so
    the check is for everything this feature adds: the SDK, the exporter, the instrumentations,
    the semantic conventions, the protobuf definitions and ``redis``.
    """
    code = (
        "import sys; from your_service_name.backend.main import create_app; create_app();"
        "banned = ('opentelemetry.sdk', 'opentelemetry.exporter', 'opentelemetry.instrumentation',"
        " 'opentelemetry.semconv', 'opentelemetry.proto', 'opentelemetry.util.http', 'redis');"
        "bad = sorted(m for m in sys.modules if m == 'redis' or m.startswith(banned));"
        "assert not bad, bad"
    )
    result = _import(code, {})
    assert result.returncode == 0, result.stderr
