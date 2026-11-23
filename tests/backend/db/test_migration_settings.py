# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The migration runner keeps the production database checks, and only those.

The chart's Job passes ENV and the database settings but not the app's other settings, so the full
production check set of ``Settings`` cannot run there. ``Settings.for_migration`` runs the database
checks alone when ENV is production, so a weak TLS mode to a remote host is refused before any
connection (and in development nothing is checked).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr

from your_service_name.backend.settings import Settings

REMOTE = "postgresql+asyncpg://u:p@db.example.test:5432/app"
LOOPBACK = "postgresql+asyncpg://u:p@127.0.0.1:5432/app"
ROOT = Path(__file__).resolve().parents[3]


def _env(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for name in (
        "ENV",
        "DATABASE_URL",
        "DB_SSLMODE",
        "TEST_DATABASE_URL",
        "MIGRATION_TEST_DELAY_SECONDS",
        "MIGRATION_TEST_FAIL",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize("mode", ["require", "disable"])
def test_production_refuses_weak_tls_to_a_remote_host(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    _env(monkeypatch, ENV="production", DATABASE_URL=REMOTE, DB_SSLMODE=mode)
    with pytest.raises(ValueError, match=f"DB_SSLMODE={mode}"):
        Settings.for_migration()


@pytest.mark.parametrize("mode", ["verify-full", "verify-ca"])
def test_production_with_verified_tls_proceeds(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    _env(monkeypatch, ENV="production", DATABASE_URL=REMOTE, DB_SSLMODE=mode)
    assert mode == Settings.for_migration().DB_SSLMODE


def test_production_accepts_disable_to_loopback_the_proxy_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Auth Proxy sidecar: the hop is local, so the app's own check accepts `disable`."""
    _env(monkeypatch, ENV="prod", DATABASE_URL=LOOPBACK, DB_SSLMODE="disable")
    assert Settings.for_migration().DB_SSLMODE == "disable"
    assert (
        Settings(
            ENV="development", DATABASE_URL=SecretStr(LOOPBACK), DB_SSLMODE="disable"
        ).database_problems()
        == []
    )


@pytest.mark.parametrize(
    "hook", [{"MIGRATION_TEST_DELAY_SECONDS": "160"}, {"MIGRATION_TEST_FAIL": "true"}]
)
def test_production_refuses_the_test_only_migration_hooks(
    monkeypatch: pytest.MonkeyPatch, hook: dict[str, str]
) -> None:
    _env(monkeypatch, ENV="production", DATABASE_URL=REMOTE, DB_SSLMODE="verify-full", **hook)
    with pytest.raises(ValueError, match="test-only"):
        Settings.for_migration()


def test_production_refuses_a_missing_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, ENV="production", DB_SSLMODE="verify-full")
    with pytest.raises(ValueError, match="DATABASE_URL"):
        Settings.for_migration()


def test_development_checks_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, DATABASE_URL=REMOTE, DB_SSLMODE="require")
    assert Settings.for_migration().DB_SSLMODE == "require"


def test_the_apps_other_production_checks_do_not_trip_the_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ENV=production with no auth, hosts or proxy settings: only the database matters here."""
    _env(monkeypatch, ENV="production", DATABASE_URL=REMOTE, DB_SSLMODE="verify-full")
    Settings.for_migration()
    with pytest.raises(ValueError, match="invalid configuration"):
        Settings()


def test_the_job_entry_point_exits_non_zero_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _env(monkeypatch)
    env = {
        **os.environ,
        "ENV": "production",
        "DATABASE_URL": REMOTE,
        "DB_SSLMODE": "require",
    }
    try:
        result = subprocess.run(
            [sys.executable, "-m", "your_service_name.backend.db.run_migrations"],
            capture_output=True,
            text=True,
            check=False,
            cwd=ROOT,
            env=env,
            timeout=10,  # a refusal is immediate; connecting would retry for over a minute
        )
    except subprocess.TimeoutExpired:
        pytest.fail("the runner tried to connect instead of refusing before any connection")
    assert result.returncode == 1
    assert "DB_SSLMODE=require" in result.stderr
    assert "attempt" not in result.stderr  # no connection was tried
