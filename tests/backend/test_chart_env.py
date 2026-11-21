# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The chart must put the production validators on, and must not fail them, in production."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from your_service_name.backend.settings import Settings

CHART = Path(__file__).resolve().parents[2] / "charts" / "your-service-name"
# The chart assembles DATABASE_URL for the backend (templates/_helpers.tpl); any Postgres URL
# stands in for it here. Obviously fake.
CHART_DATABASE_URL = "postgresql+asyncpg://app:fake@db.example.test:5432/app"


def _config(env: str) -> dict[str, Any]:
    """The ConfigMap values the backend receives for ``env``: values.yaml + values.<env>.yaml."""
    base = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))["config"]
    over = yaml.safe_load((CHART / f"values.{env}.yaml").read_text(encoding="utf-8")) or {}
    return {**base, **over.get("config", {})}


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


@pytest.mark.parametrize("env", ["dev", "qa", "production", "local"])
def test_every_environment_configuration_starts_the_backend(env: str) -> None:
    """The ConfigMap + chart-assembled DATABASE_URL pass Settings validation in each env."""
    config = {k: v for k, v in _config(env).items() if k in Settings.model_fields}
    config.setdefault("DATABASE_URL", CHART_DATABASE_URL)
    Settings(**config)
