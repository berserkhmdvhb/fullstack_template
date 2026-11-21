# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Settings for the opt-in CSP report endpoint."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from your_service_name.backend.settings import Settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)


def test_defaults() -> None:
    s = Settings()
    assert s.CSP_REPORT_ENABLED is False
    assert s.CSP_REPORT_MAX_BYTES == 16384  # noqa: PLR2004
    assert s.CSP_REPORT_RATE_LIMIT == "20/minute"


def test_blank_values_are_the_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CSP_REPORT_MAX_BYTES", "")
    monkeypatch.setenv("CSP_REPORT_RATE_LIMIT", "")
    assert Settings().CSP_REPORT_MAX_BYTES == 16384  # noqa: PLR2004
    assert Settings().CSP_REPORT_RATE_LIMIT == "20/minute"


@pytest.mark.parametrize("value", ["0", "-5", "2000000", "x"])
def test_max_bytes_range(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("CSP_REPORT_MAX_BYTES", value)
    with pytest.raises(ValidationError, match="CSP_REPORT_MAX_BYTES"):
        Settings()


def test_rate_limit_must_be_a_limits_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CSP_REPORT_RATE_LIMIT", "often")
    with pytest.raises(ValidationError, match="CSP_REPORT_RATE_LIMIT must be"):
        Settings()
