# your-service-name — adaptable service template (see wiki/getting-started.md)
from __future__ import annotations

import pytest
from pydantic import ValidationError

from your_service_name.backend.schemas.health import HealthStatus


def test_health_status_literal_ok_only() -> None:
    ok = HealthStatus(status="ok")
    assert ok.status == "ok"

    # Invalid case: use model_validate to avoid mypy Literal type error
    with pytest.raises(ValidationError, match="status"):
        HealthStatus.model_validate({"status": "healthy"})  # must be Literal["ok"]


def test_health_status_forbids_extra_fields() -> None:
    # Invalid case: extra field triggers ValidationError
    with pytest.raises(ValidationError, match="extra"):
        HealthStatus.model_validate({"status": "ok", "extra": "nope"})  # extra="forbid"
