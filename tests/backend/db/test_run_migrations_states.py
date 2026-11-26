# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The migration runner's four states: at head, behind, ahead of this release, multiple heads."""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
import subprocess
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.util.exc import CommandError

from tests.utils.migration_scripts import make_script_dir, revision_of, stamp
from your_service_name.backend.db import run_migrations as runner
from your_service_name.backend.db import schema
from your_service_name.backend.settings import Settings

AHEAD_MESSAGE = "database is ahead of this release; nothing to do"
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in Settings.model_fields:
        if name != "TEST_DATABASE_URL":
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def url() -> str:
    found = Settings().database_url
    assert found
    return found


def _use(
    monkeypatch: pytest.MonkeyPatch, root: Path, revisions: list[tuple[str, str | None]]
) -> None:
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", make_script_dir(root, revisions))


async def test_an_empty_database_is_upgraded_to_head(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, url: str
) -> None:
    _use(monkeypatch, tmp_path, [("0001", None), ("0002", "0001")])
    await runner.run_migrations(Settings())
    assert revision_of(url) == "0002"


async def test_a_database_at_head_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, url: str, caplog: pytest.LogCaptureFixture
) -> None:
    _use(monkeypatch, tmp_path, [("0001", None), ("0002", "0001")])
    await runner.run_migrations(Settings())
    calls: list[str] = []
    monkeypatch.setattr(command, "upgrade", lambda *_a, **_k: calls.append("upgrade"))
    with caplog.at_level(logging.INFO):
        await runner.run_migrations(Settings())
    assert calls == []
    assert "already at head" in caplog.text
    assert revision_of(url) == "0002"


async def test_a_database_behind_is_upgraded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, url: str
) -> None:
    _use(monkeypatch, tmp_path / "old", [("0001", None)])
    await runner.run_migrations(Settings())
    assert revision_of(url) == "0001"
    _use(monkeypatch, tmp_path / "new", [("0001", None), ("0002", "0001")])
    await runner.run_migrations(Settings())
    assert revision_of(url) == "0002"


async def test_a_database_ahead_is_left_alone_and_does_not_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, url: str, caplog: pytest.LogCaptureFixture
) -> None:
    _use(monkeypatch, tmp_path, [("0001", None)])
    stamp(url, "9999_from_a_newer_release")
    calls: list[str] = []
    monkeypatch.setattr(command, "upgrade", lambda *_a, **_k: calls.append("upgrade"))
    monkeypatch.setattr(command, "downgrade", lambda *_a, **_k: calls.append("downgrade"))
    with caplog.at_level(logging.INFO):
        await runner.run_migrations(Settings())
    assert AHEAD_MESSAGE in caplog.text
    assert calls == []
    assert revision_of(url) == "9999_from_a_newer_release"


async def test_multiple_heads_fail_before_touching_the_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, url: str
) -> None:
    _use(monkeypatch, tmp_path, [("0001", None), ("0002a", "0001"), ("0002b", "0001")])
    with pytest.raises(RuntimeError, match="multiple heads"):
        await runner.run_migrations(Settings())
    assert revision_of(url) is None


async def test_the_test_only_delay_sleeps_before_migrating(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, url: str
) -> None:
    """MIGRATION_TEST_DELAY_SECONDS (the kind test's slow migration): a sleep, then the upgrade."""
    _use(monkeypatch, tmp_path, [("0001", None)])
    order: list[str] = []

    async def sleep(seconds: float) -> None:
        order.append(f"sleep {seconds}")

    real_upgrade = command.upgrade

    def upgrade(*args: object, **kwargs: object) -> None:
        order.append("upgrade")
        real_upgrade(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(asyncio, "sleep", sleep)
    monkeypatch.setattr(command, "upgrade", upgrade)
    await runner.run_migrations(Settings(MIGRATION_TEST_DELAY_SECONDS=7))
    assert order == ["sleep 7", "upgrade"]
    assert revision_of(url) == "0001"


async def test_the_test_only_failure_fails_before_migrating(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, url: str
) -> None:
    """MIGRATION_TEST_FAIL (the kind test's failed migration): the database is left untouched."""
    _use(monkeypatch, tmp_path, [("0001", None)])
    with pytest.raises(RuntimeError, match="MIGRATION_TEST_FAIL: failing on purpose"):
        await runner.run_migrations(Settings(MIGRATION_TEST_FAIL=True))
    assert revision_of(url) is None


async def test_the_test_only_hooks_are_off_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _use(monkeypatch, tmp_path, [("0001", None)])
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    settings = Settings()
    assert (settings.MIGRATION_TEST_DELAY_SECONDS, settings.MIGRATION_TEST_FAIL) == (0, False)
    await runner.run_migrations(settings)
    assert slept == []


def test_the_module_exits_zero_when_the_database_is_ahead(url: str) -> None:
    """`python -m ...run_migrations` (what the Job runs) reports success on a newer schema."""
    stamp(url, "9999_from_a_newer_release")
    env = {**os.environ, "TEST_DATABASE_URL": url}
    done = subprocess.run(
        [sys.executable, "-m", "your_service_name.backend.db.run_migrations"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=ROOT,
    )
    assert done.returncode == 0, done.stderr
    assert AHEAD_MESSAGE in done.stdout + done.stderr


async def test_lock_timeout_is_set_and_statement_timeout_is_not() -> None:
    from your_service_name.backend.db.connect import migration_connect_args  # noqa: PLC0415

    args = migration_connect_args(Settings(DB_MIGRATION_LOCK_TIMEOUT_MS=4321))
    assert args["server_settings"] == {"lock_timeout": "4321"}
    assert "statement_timeout" not in str(args)
    assert "idle_in_transaction_session_timeout" not in str(args)


async def test_the_migration_connection_has_the_lock_timeout_and_tls_on_postgresql_only() -> None:
    from your_service_name.backend.db.connect import migration_connect_args  # noqa: PLC0415

    args = migration_connect_args(Settings(DB_SSLMODE="require", DB_CONNECT_TIMEOUT_SECONDS=3))
    assert args["timeout"] == 3  # noqa: PLR2004
    assert args["ssl"] is not False
    assert runner._connect_args("sqlite+aiosqlite:///x.db", Settings()) == {}
    assert runner._connect_args("postgresql+asyncpg://u:p@h/db", Settings())["server_settings"]


async def test_connect_failures_are_retried_a_bounded_number_of_times(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    attempts: list[int] = []

    class Boom:
        def connect(self) -> Boom:
            attempts.append(1)
            msg = "could not connect to postgresql+asyncpg://u:SECRETPW@h/db"
            raise OSError(msg)

        async def dispose(self) -> None: ...

    async def no_sleep(_delay: float) -> None: ...

    monkeypatch.setattr(runner, "_create_engine", lambda *_a, **_k: Boom())
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr(runner, "_DB_INIT_MAX_ATTEMPTS", 3)
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+asyncpg://u:SECRETPW@h/db")
    with caplog.at_level(logging.INFO), pytest.raises(RuntimeError) as excinfo:
        await runner.run_migrations(Settings())
    assert len(attempts) == 3  # noqa: PLR2004
    assert "attempt 1/3" in caplog.text
    assert "SECRETPW" not in caplog.text
    assert "SECRETPW" not in str(excinfo.value)


async def _attempts_with(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> tuple[list[int], BaseException]:
    attempts: list[int] = []

    class Boom:
        def connect(self) -> Boom:
            attempts.append(1)
            raise error

        async def dispose(self) -> None: ...

    async def no_sleep(_delay: float) -> None: ...

    monkeypatch.setattr(runner, "_create_engine", lambda *_a, **_k: Boom())
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr(runner, "_DB_INIT_MAX_ATTEMPTS", 3)
    with pytest.raises(RuntimeError) as excinfo:
        await runner.run_migrations(Settings())
    return attempts, excinfo.value


def _wrapped(orig: BaseException) -> BaseException:
    from sqlalchemy.exc import OperationalError  # noqa: PLC0415

    return OperationalError("SELECT 1", {}, orig)


@pytest.mark.parametrize(
    "error",
    [
        ConnectionRefusedError("refused"),
        TimeoutError("timed out"),
        OSError("unreachable"),
        _wrapped(ConnectionRefusedError("refused")),
        _wrapped(OSError("down")),
    ],
)
async def test_connection_class_errors_are_retried(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    attempts, _ = await _attempts_with(monkeypatch, error)
    assert len(attempts) == 3  # noqa: PLR2004


@pytest.mark.parametrize(
    "error",
    [
        ssl.SSLCertVerificationError("certificate verify failed"),
        FileNotFoundError("no CA file"),
        _wrapped(ValueError("password authentication failed for user")),
        CommandError("several rows"),
        ValueError("anything else"),
    ],
)
async def test_other_errors_fail_at_once_without_retry(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    attempts, _ = await _attempts_with(monkeypatch, error)
    assert len(attempts) == 1


async def test_asyncpg_authentication_failure_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncpg  # noqa: PLC0415

    attempts, _ = await _attempts_with(
        monkeypatch, asyncpg.InvalidPasswordError("password authentication failed")
    )
    assert len(attempts) == 1


async def test_asyncpg_connection_errors_are_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncpg  # noqa: PLC0415

    attempts, _ = await _attempts_with(
        monkeypatch, asyncpg.CannotConnectNowError("the database system is starting up")
    )
    assert len(attempts) == 3  # noqa: PLR2004


async def _failure(settings: Settings) -> str:
    with pytest.raises(RuntimeError) as excinfo:
        await runner.run_migrations(settings)
    assert excinfo.value.__cause__ is None  # the chain stays dropped (it can echo the URL)
    return str(excinfo.value)


async def test_a_scripts_problem_names_its_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _use(monkeypatch, tmp_path, [("0001", None), ("0002a", "0001"), ("0002b", "0001")])
    message = await _failure(Settings())
    assert message.startswith("migration failed while loading the migration scripts: ")
    assert "multiple heads" in message


async def test_a_failed_sqlite_dir_names_its_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _use(monkeypatch, tmp_path, [("0001", None)])

    def boom(*_a: object) -> None:
        msg = "read-only file system"
        raise OSError(msg)

    monkeypatch.setattr(runner, "_ensure_sqlite_dir", boom)
    message = await _failure(Settings())
    assert message == (
        "migration failed while preparing the database directory: OSError: read-only file system"
    )


async def test_a_failed_revision_read_names_its_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _use(monkeypatch, tmp_path, [("0001", None)])

    async def boom(*_a: object) -> None:
        msg = "no route"
        raise ValueError(msg)

    monkeypatch.setattr(runner, "_read_revision_with_retry", boom)
    message = await _failure(Settings())
    assert message == "migration failed while reading the database revision: ValueError: no route"


async def test_a_failed_test_hook_names_its_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _use(monkeypatch, tmp_path, [("0001", None)])
    message = await _failure(Settings(MIGRATION_TEST_FAIL=True))
    assert message.startswith("migration failed while running the test hook: RuntimeError: ")


async def test_a_failed_upgrade_names_its_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _use(monkeypatch, tmp_path, [("0001", None)])

    def boom(*_a: object, **_k: object) -> None:
        msg = "disk full"
        raise OSError(msg)

    monkeypatch.setattr(command, "upgrade", boom)
    message = await _failure(Settings())
    assert message == "migration failed while applying the migrations: OSError: disk full"


async def test_an_error_without_text_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A bare TimeoutError (what a connect timeout raises) still tells the operator something."""
    _use(monkeypatch, tmp_path, [("0001", None)])

    def boom(*_a: object, **_k: object) -> None:
        raise TimeoutError

    monkeypatch.setattr(command, "upgrade", boom)
    message = await _failure(Settings())
    assert message == "migration failed while applying the migrations: TimeoutError: no detail"
