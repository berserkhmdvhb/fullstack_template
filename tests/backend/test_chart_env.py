# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The chart must put the production validators on, and must not fail them, in production."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from tests.backend.test_chart_request_limits import rendered_config
from your_service_name.backend.settings import Settings

CHART = Path(__file__).resolve().parents[2] / "charts" / "your-service-name"
# The chart assembles DATABASE_URL for the backend (templates/_helpers.tpl); any Postgres URL
# stands in for it here. Obviously fake.
CHART_DATABASE_URL = "postgresql+asyncpg://app:fake@db.example.test:5432/app"


def _config(env: str) -> dict[str, Any]:
    """The ConfigMap values the backend receives for ``env``: values.yaml + values.<env>.yaml,
    plus ALLOWED_HOSTS, which the chart computes (so it is read from the rendered ConfigMap)."""
    base = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))["config"]
    over = yaml.safe_load((CHART / f"values.{env}.yaml").read_text(encoding="utf-8")) or {}
    rendered = rendered_config(env)
    return {**base, **over.get("config", {}), "ALLOWED_HOSTS": rendered["ALLOWED_HOSTS"]}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)


def test_production_values_set_env_production() -> None:
    """Without ENV=production the production validators would silently stay off."""
    assert _config("production")["ENV"] == "production"


@pytest.mark.parametrize("env", ["dev", "qa", "local"])
def test_other_environments_are_not_production(env: str) -> None:
    assert not Settings(ENV=_config(env)["ENV"]).is_production


def _chart_settings(env: str, **extra: str) -> Settings:
    """Settings as the backend sees them in ``env``: ConfigMap + chart DATABASE_URL + ``extra``."""
    config = {k: v for k, v in _config(env).items() if k in Settings.model_fields}
    config.setdefault("DATABASE_URL", CHART_DATABASE_URL)
    return Settings(**{**config, **extra})


@pytest.mark.parametrize("env", ["dev", "qa", "local"])
def test_non_production_configuration_starts_the_backend(env: str) -> None:
    """The ConfigMap + chart-assembled DATABASE_URL pass Settings validation."""
    _chart_settings(env)


def test_production_refuses_to_start_until_an_auth_mode_is_chosen() -> None:
    """The template does not choose anonymous production for the adopter; only auth is missing."""
    with pytest.raises(ValidationError) as excinfo:
        _chart_settings("production")
    text = str(excinfo.value)
    assert "AUTH_MODE" in text
    assert "ALLOW_ANONYMOUS" in text
    for other in ("DATABASE_URL", "CORS_", "API_DOCS_URL", "OPENAPI_URL", "TEST_DATABASE_URL"):
        assert other not in text


def test_production_starts_once_an_auth_mode_is_added() -> None:
    assert _chart_settings("production", AUTH_MODE="gateway").AUTH_MODE == "gateway"


def test_production_values_do_not_opt_in_to_anonymous() -> None:
    assert "ALLOW_ANONYMOUS" not in _config("production")
