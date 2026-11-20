# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Tests for EchoService.

Covers:
- echo(text): returns (text, length), calls repo.save()
- list_history(): returns list(repo.list())
- Exception handling: logs and re-raises on repo failure
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from your_service_name.backend.repositories.base import EchoItem, EchoRepository
from your_service_name.backend.services.echo_service import EchoService

EXPECTED_STORAGE_COUNT: int = 2
EXPECTED_ECHO_COUNT: int = 2

# ---------------------------------------------------------------------------
# Fake Repository
# ---------------------------------------------------------------------------


class _FakeEchoRepository:
    """In-memory fake EchoRepository for testing."""

    def __init__(self) -> None:
        self._storage: list[EchoItem] = []

    def save(self, text: str, length: int) -> None:
        """Save an echo item."""
        self._storage.append((text, length))

    def list(self) -> list[EchoItem]:
        """List all echo items."""
        return list(self._storage)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_repo() -> _FakeEchoRepository:
    """Create a fresh fake repository."""
    return _FakeEchoRepository()


@pytest.fixture
def service(fake_repo: _FakeEchoRepository) -> EchoService:
    """Create an EchoService with fake repository."""
    return EchoService(repo=fake_repo)


class TestEchoServiceEcho:
    """Test EchoService.echo() method."""

    def test_echo_returns_text_and_length(self, service: EchoService) -> None:
        """echo() should return (text, length) tuple."""
        result = service.echo("hello")

        assert result == ("hello", 5)

    def test_echo_empty_string(self, service: EchoService) -> None:
        """echo() should handle empty string."""
        result = service.echo("")

        assert result == ("", 0)

    def test_echo_unicode(self, service: EchoService) -> None:
        """echo() should handle unicode text."""
        result = service.echo("héllo")

        assert result == ("héllo", 5)

    def test_echo_calls_repo_save(
        self, service: EchoService, fake_repo: _FakeEchoRepository
    ) -> None:
        """echo() should call repo.save() with text and length."""
        service.echo("test")

        assert len(fake_repo._storage) == 1
        assert fake_repo._storage[0] == ("test", 4)

    def test_echo_multiple_calls(
        self, service: EchoService, fake_repo: _FakeEchoRepository
    ) -> None:
        """echo() should accumulate results."""
        service.echo("first")
        service.echo("second")

        assert len(fake_repo._storage) == EXPECTED_ECHO_COUNT
        assert fake_repo._storage[0] == ("first", 5)
        assert fake_repo._storage[1] == ("second", 6)


class TestEchoServiceListHistory:
    """Test EchoService.list_history() method."""

    def test_list_history_empty(self, service: EchoService) -> None:
        """list_history() should return empty list when no items."""
        result = service.list_history()

        assert result == []

    def test_list_history_with_items(self, service: EchoService) -> None:
        """list_history() should return all items."""
        service.echo("first")
        service.echo("second")

        result = service.list_history()

        assert len(result) == EXPECTED_ECHO_COUNT
        assert result[0] == ("first", 5)
        assert result[1] == ("second", 6)

    def test_list_history_returns_copy(
        self, service: EchoService, fake_repo: _FakeEchoRepository
    ) -> None:
        """list_history() should return a copy (defensive)."""
        service.echo("test")

        result = service.list_history()
        result.append(("modified", 8))

        # Original should be unchanged
        assert len(fake_repo._storage) == 1


# ---------------------------------------------------------------------------
# Tests: Exception handling
# ---------------------------------------------------------------------------


class TestEchoServiceExceptions:
    """Test EchoService exception handling."""

    def test_echo_reraises_repo_exception(self) -> None:
        """echo() should re-raise exceptions from repo."""
        mock_repo = MagicMock(spec=EchoRepository)
        mock_repo.save.side_effect = RuntimeError("DB error")

        service = EchoService(repo=mock_repo)

        with pytest.raises(RuntimeError, match="DB error"):
            service.echo("test")

    def test_list_history_reraises_repo_exception(self) -> None:
        """list_history() should re-raise exceptions from repo."""
        mock_repo = MagicMock(spec=EchoRepository)
        mock_repo.list.side_effect = RuntimeError("DB error")

        service = EchoService(repo=mock_repo)

        with pytest.raises(RuntimeError, match="DB error"):
            service.list_history()
