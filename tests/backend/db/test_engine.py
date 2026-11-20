# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Tests for database engine module.

Covers:
- _resolve_database_url(): precedence (TEST_DATABASE_URL > DATABASE_URL > SQLite fallback)
- get_session(): yields session, commits on success, rolls back on exception
- init_db() / close_db(): lifecycle management
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from your_service_name.backend.db import engine as engine_module
from your_service_name.backend.db.engine import (
    _resolve_database_url,
    close_db,
    get_session,
    init_db,
)


class TestResolveDatabaseUrl:
    """Test database URL resolution precedence."""

    def test_test_database_url_takes_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TEST_DATABASE_URL should override DATABASE_URL."""
        monkeypatch.setenv("TEST_DATABASE_URL", "sqlite+aiosqlite:///test.db")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@host/db")

        url = _resolve_database_url()

        assert url == "sqlite+aiosqlite:///test.db"

    def test_database_url_used_when_test_url_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DATABASE_URL should be used when TEST_DATABASE_URL is not set."""
        monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@host/db")

        url = _resolve_database_url()

        assert url == "postgresql+asyncpg://user:pass@host/db"

    def test_sqlite_fallback_when_no_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should fall back to SQLite when no env vars are set."""
        monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)

        url = _resolve_database_url()

        assert url.startswith("sqlite+aiosqlite:///")
        assert "items.db" in url

    def test_sqlite_fallback_creates_data_directory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SQLite fallback should create the data/ directory if it doesn't exist."""
        monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)

        url = _resolve_database_url()

        # Extract the path from the URL
        db_path_str = url.replace("sqlite+aiosqlite:///", "")
        db_path = Path(db_path_str)

        # The parent directory should exist (created by the function)
        assert db_path.parent.exists()


class TestGetSession:
    """Test session generator behavior."""

    def _setup_mock_factory(self) -> tuple[AsyncMock, AsyncMock]:
        """Helper to set up mock session factory."""
        mock_session = AsyncMock(spec=AsyncSession)
        # Set up async context manager
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock()
        mock_factory.return_value = mock_session

        engine_module._session_factory = mock_factory
        return mock_factory, mock_session

    def _cleanup_mock_factory(self) -> None:
        """Helper to clean up mock session factory."""
        engine_module._session_factory = None

    @pytest.mark.asyncio
    async def test_get_session_yields_session(self) -> None:
        """get_session should yield a session."""
        _, _mock_session = self._setup_mock_factory()
        try:
            async for session in get_session():
                assert session is not None
        finally:
            self._cleanup_mock_factory()

    @pytest.mark.asyncio
    async def test_get_session_commits_on_success(self) -> None:
        """get_session should commit on successful completion."""
        _, mock_session = self._setup_mock_factory()
        try:
            async for _session in get_session():
                pass

            mock_session.commit.assert_called_once()
        finally:
            self._cleanup_mock_factory()

    @pytest.mark.asyncio
    async def test_get_session_rolls_back_on_exception(self) -> None:
        """get_session should rollback on exception."""
        _, mock_session = self._setup_mock_factory()
        mock_session.commit.side_effect = Exception("Test error")

        try:
            with pytest.raises(Exception, match="Test error"):
                async for _session in get_session():
                    pass

            mock_session.rollback.assert_called_once()
        finally:
            self._cleanup_mock_factory()

    @pytest.mark.asyncio
    async def test_get_session_raises_when_factory_none(self) -> None:
        """get_session should raise RuntimeError when session factory is None."""
        engine_module._session_factory = None

        with pytest.raises(RuntimeError, match="Session factory is not initialized"):
            async for _session in get_session():
                pass


class TestDatabaseLifecycle:
    """Test database initialization and cleanup."""

    @pytest.mark.asyncio
    async def test_init_db_calls_migrations(self) -> None:
        """init_db should call run_migrations."""
        with patch(
            "your_service_name.backend.db.engine.run_migrations", new_callable=AsyncMock
        ) as mock_migrations:
            mock_app = AsyncMock()
            await init_db(mock_app)

            mock_migrations.assert_called_once()

    @pytest.mark.asyncio
    async def test_init_db_sets_initialized_flag(self) -> None:
        """init_db should set _db_initialized to True."""
        with patch("your_service_name.backend.db.engine.run_migrations", new_callable=AsyncMock):
            mock_app = AsyncMock()
            await init_db(mock_app)

            assert engine_module._db_initialized is True

    @pytest.mark.asyncio
    async def test_close_db_disposes_engine(self) -> None:
        """close_db should dispose the engine and reset state."""
        # First initialize
        with patch("your_service_name.backend.db.engine.run_migrations", new_callable=AsyncMock):
            mock_app = AsyncMock()
            await init_db(mock_app)

        # Then close
        mock_engine = AsyncMock()
        engine_module._engine = mock_engine

        await close_db(mock_app)

        mock_engine.dispose.assert_called_once()
        assert engine_module._engine is None
        assert engine_module._session_factory is None
        assert engine_module._db_initialized is False
