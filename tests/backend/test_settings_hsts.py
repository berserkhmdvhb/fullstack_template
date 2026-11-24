# your-service-name — adaptable service template (see wiki/getting-started.md)
"""HSTS_ENABLED: on by default; off where the edge sets HSTS (one source per deployment)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from your_service_name.backend.main import create_app
from your_service_name.backend.settings import Settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)


def test_default_is_on() -> None:
    assert Settings().HSTS_ENABLED is True


def test_blank_is_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HSTS_ENABLED", "")
    assert Settings().HSTS_ENABLED is True


def test_false_turns_it_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HSTS_ENABLED", "false")
    assert Settings().HSTS_ENABLED is False


@pytest.mark.parametrize("value", ["true", "false"])
def test_the_app_follows_the_setting_over_https(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    sent = value == "true"
    monkeypatch.setenv("HSTS_ENABLED", value)
    with TestClient(create_app(Settings()), base_url="https://testserver") as client:
        r = client.get("/api/v1/health")
    assert ("Strict-Transport-Security" in r.headers) is sent
