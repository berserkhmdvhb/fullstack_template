# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Tests for the correlation-id context and ``RequestIdFilter``.

Covers:
- ``generate_request_id`` is opaque and unique
- ``get``/``set``/``reset`` scoping via the ``ContextVar`` (incl. nesting + isolation)
- a missing/empty id is replaced with a generated one
- ``RequestIdFilter`` injects the active id onto records (sentinel when out of a request)
"""

from __future__ import annotations

import logging

from your_service_name.backend.logging.correlation import (
    NO_REQUEST_ID,
    RequestIdFilter,
    generate_request_id,
    get_request_id,
    reset_request_id,
    set_request_id,
)

# A generated id is a UUID4 hex string (uuid4().hex): 32 lowercase hex chars, no dashes.
_UUID4_HEX_LEN = 32


def _record(message: str = "m") -> logging.LogRecord:
    """Build a bare log record for filter assertions."""
    return logging.LogRecord("test.logger", logging.INFO, __file__, 1, message, (), None)


class TestGenerateRequestId:
    """``generate_request_id`` produces opaque, unique ids."""

    def test_is_opaque_hex(self) -> None:
        """The id is 32 hex chars — a UUID4 without dashes."""
        request_id = generate_request_id()
        assert len(request_id) == _UUID4_HEX_LEN
        assert all(char in "0123456789abcdef" for char in request_id)

    def test_is_unique(self) -> None:
        """Two calls do not collide."""
        assert generate_request_id() != generate_request_id()


class TestGetSetReset:
    """The correlation id is a request-scoped contextvar."""

    def test_default_is_no_request_id(self) -> None:
        """Out of any request, the id is the sentinel."""
        assert get_request_id() == NO_REQUEST_ID

    def test_set_then_reset(self) -> None:
        """``set`` returns a token that restores the previous value."""
        token = set_request_id("abc123")
        try:
            assert get_request_id() == "abc123"
        finally:
            reset_request_id(token)
        assert get_request_id() == NO_REQUEST_ID

    def test_missing_id_is_generated(self) -> None:
        """``None`` or empty string yields a fresh id — never blank."""
        for missing in (None, ""):
            token = set_request_id(missing)
            try:
                resolved = get_request_id()
                assert resolved != NO_REQUEST_ID
                assert resolved != ""
            finally:
                reset_request_id(token)

    def test_nested_contexts_are_isolated(self) -> None:
        """An inner id does not leak into the outer context after it is reset."""
        outer = set_request_id("outer")
        try:
            inner = set_request_id("inner")
            assert get_request_id() == "inner"
            reset_request_id(inner)
            assert get_request_id() == "outer"
        finally:
            reset_request_id(outer)


class TestRequestIdFilter:
    """``RequestIdFilter`` injects the active id onto every record."""

    def test_injects_active_id(self) -> None:
        """``record.request_id`` becomes the active id; the filter never suppresses."""
        token = set_request_id("req-1")
        try:
            record = _record()
            assert RequestIdFilter().filter(record) is True
            # ``request_id`` is added dynamically by the filter, so read it via getattr.
            assert getattr(record, "request_id") == "req-1"  # noqa: B009
        finally:
            reset_request_id(token)

    def test_defaults_when_out_of_request(self) -> None:
        """With no active id, the sentinel is injected."""
        record = _record()
        assert RequestIdFilter().filter(record) is True
        # ``request_id`` is added dynamically by the filter, so read it via getattr.
        assert getattr(record, "request_id") == NO_REQUEST_ID  # noqa: B009
