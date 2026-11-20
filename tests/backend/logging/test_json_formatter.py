# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Tests for :class:`JsonLogFormatter`.

Covers:
- output is a single line of valid JSON
- the base fields (``@timestamp``/``level``/``logger``/``message``/``request_id``) are present
- ``request_id`` is surfaced (and falls back to the sentinel when absent)
- ``extra=`` fields are merged as structured fields
- reserved keys (``message``/``request_id``/…) can't be clobbered by ``extra=``
"""

from __future__ import annotations

import json
import logging

from your_service_name.backend.logging.correlation import NO_REQUEST_ID
from your_service_name.backend.logging.json_formatter import JsonLogFormatter

_ITEM_ID = 7


def _record(message: str = "hello", extra: dict[str, object] | None = None) -> logging.LogRecord:
    """Build a log record (optionally carrying ``extra`` fields) for formatter assertions."""
    record = logging.LogRecord("test.logger", logging.INFO, __file__, 1, message, (), None)
    for key, value in (extra or {}).items():
        setattr(record, key, value)
    return record


class TestJsonLogFormatter:
    """The formatter renders a compact, structured JSON object per record."""

    def setup_method(self) -> None:
        self.formatter = JsonLogFormatter()

    def test_is_single_line_valid_json(self) -> None:
        """Output is one line of parseable JSON — no trailing newline."""
        out = self.formatter.format(_record())
        assert "\n" not in out
        payload = json.loads(out)
        assert isinstance(payload, dict)

    def test_base_fields_present(self) -> None:
        """The five base fields are always emitted."""
        payload = json.loads(self.formatter.format(_record("boom")))
        for field in ("@timestamp", "level", "logger", "message", "request_id"):
            assert field in payload
        assert payload["level"] == "INFO"
        assert payload["logger"] == "test.logger"
        assert payload["message"] == "boom"

    def test_request_id_surfaced(self) -> None:
        """An explicit ``request_id`` attribute is surfaced as a first-class field."""
        record = _record()
        record.request_id = "req-42"
        assert json.loads(self.formatter.format(record))["request_id"] == "req-42"

    def test_request_id_defaults_to_sentinel(self) -> None:
        """A record with no ``request_id`` attribute falls back to the sentinel."""
        assert json.loads(self.formatter.format(_record()))["request_id"] == NO_REQUEST_ID

    def test_extra_fields_are_merged(self) -> None:
        """``extra=`` fields land as structured fields on the payload."""
        payload = json.loads(
            self.formatter.format(_record(extra={"item_id": _ITEM_ID, "op": "create"}))
        )
        assert payload["item_id"] == _ITEM_ID
        assert payload["op"] == "create"

    def test_reserved_keys_are_not_clobbered(self) -> None:
        """``extra=`` cannot overwrite fields the formatter owns (``message``/``request_id``)."""
        record = _record("real message")
        record.request_id = "real-id"
        # Attempting to clobber a reserved field must have no effect on the output.
        record.message = "clobber attempt"
        payload = json.loads(self.formatter.format(record))
        assert payload["message"] == "real message"
        assert payload["request_id"] == "real-id"
