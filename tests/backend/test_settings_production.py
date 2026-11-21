# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Production validators: with ``ENV=production`` an unsafe configuration refuses to start."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from your_service_name.backend.main import create_app
from your_service_name.backend.settings import Settings

FAKE_DB_PASSWORD = "fake-db-password-not-real"  # noqa: S105
POSTGRES_URL = "postgresql+asyncpg://app:fake@db.example.test:5432/app"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start from an empty environment (conftest sets TEST_DATABASE_URL for every test)."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)


def _production(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    """A valid production base, then overrides: Postgres, gateway auth."""
    base = {
        "ENV": "production",
        "DATABASE_URL": POSTGRES_URL,
        "DB_SSLMODE": "verify-full",
        "AUTH_MODE": "gateway",
        "ALLOWED_HOSTS": "api.example.com",
    }
    for name, value in {**base, **env}.items():
        monkeypatch.setenv(name, value)


def _refused(monkeypatch: pytest.MonkeyPatch, *names: str, **env: str) -> str:
    """Build Settings expecting a refusal; assert every name is in the message; return it."""
    _production(monkeypatch, **env)
    with pytest.raises(ValidationError) as excinfo:
        Settings()
    text = str(excinfo.value)
    for name in names:
        assert name in text
    return text


class TestAllowedHostsRule:
    @pytest.mark.parametrize("value", ["*", "api.example.com,*", ""])
    def test_a_wildcard_or_empty_list_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        _refused(monkeypatch, "ALLOWED_HOSTS", ALLOWED_HOSTS=value)

    def test_an_empty_list_object_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="ALLOWED_HOSTS"):
            Settings(
                ENV="production",
                DATABASE_URL=SecretStr(POSTGRES_URL),
                DB_SSLMODE="verify-full",
                AUTH_MODE="gateway",
                ALLOWED_HOSTS=[],
            )

    def test_listed_hosts_and_subdomain_wildcards_are_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _production(monkeypatch, ALLOWED_HOSTS="api.example.com, *.example.org")
        assert Settings().ALLOWED_HOSTS == ["api.example.com", "*.example.org"]

    def test_outside_production_the_default_is_any_host(self) -> None:
        assert Settings().ALLOWED_HOSTS == ["*"]


class TestClientIpRule:
    def test_cf_trust_with_no_trusted_proxies_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        text = _refused(monkeypatch, "TRUST_CF_CONNECTING_IP", TRUST_CF_CONNECTING_IP="true")
        assert "TRUSTED_PROXY_CIDRS" in text

    def test_cf_trust_with_trusted_proxies_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _production(monkeypatch, TRUST_CF_CONNECTING_IP="true", TRUSTED_PROXY_CIDRS="10.0.0.0/8")
        assert Settings().TRUST_CF_CONNECTING_IP

    def test_cf_trust_off_with_no_trusted_proxies_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _production(monkeypatch)
        assert Settings().TRUSTED_PROXY_CIDRS == []


class TestProductionSwitch:
    def test_a_valid_production_configuration_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _production(monkeypatch)
        assert Settings().is_production

    @pytest.mark.parametrize("value", ["production", "Production", "PRODUCTION", " prod "])
    def test_env_is_case_insensitive(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        _production(monkeypatch, ENV=value, DATABASE_URL="sqlite+aiosqlite:///x.db")
        with pytest.raises(ValidationError, match="DATABASE_URL"):
            Settings()

    @pytest.mark.parametrize("value", ["dev", "development", "qa", "local"])
    def test_other_environments_keep_todays_behaviour(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("ENV", value)
        s = Settings()  # SQLite fallback, AUTH_MODE=none, docs on: all fine outside production
        assert not s.is_production
        assert s.docs_url == "/docs"
        assert s.openapi_url == "/openapi.json"


class TestDatabase:
    @pytest.mark.parametrize(
        "url",
        ["sqlite:///x.db", "sqlite+aiosqlite:///x.db", "SQLite+pysqlite:///:memory:"],
    )
    def test_sqlite_is_refused(self, monkeypatch: pytest.MonkeyPatch, url: str) -> None:
        text = self._refused_url(monkeypatch, url)
        assert "SQLite" in text

    def test_unset_database_url_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _production(monkeypatch)
        monkeypatch.delenv("DATABASE_URL")
        with pytest.raises(ValidationError, match="DATABASE_URL"):
            Settings()

    def test_sqlite_path_does_not_make_sqlite_acceptable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _production(monkeypatch, SQLITE_PATH="data/other.db")
        monkeypatch.delenv("DATABASE_URL")
        with pytest.raises(ValidationError, match="DATABASE_URL"):
            Settings()

    def test_test_database_url_cannot_smuggle_sqlite_in(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _production(monkeypatch, TEST_DATABASE_URL="sqlite+aiosqlite:///x.db")
        with pytest.raises(ValidationError, match="DATABASE_URL"):
            Settings()

    def test_a_postgres_test_database_url_cannot_stand_in_for_database_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TEST_DATABASE_URL wins in the engine, so it must never satisfy the production check."""
        _production(monkeypatch, TEST_DATABASE_URL=POSTGRES_URL)
        monkeypatch.delenv("DATABASE_URL")
        with pytest.raises(ValidationError) as excinfo:
            Settings()
        assert "DATABASE_URL" in str(excinfo.value)
        assert "TEST_DATABASE_URL" in str(excinfo.value)

    def test_test_database_url_alongside_database_url_is_refused_not_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        text = _refused(monkeypatch, "TEST_DATABASE_URL", TEST_DATABASE_URL=POSTGRES_URL)
        assert "test-only" in text

    def test_database_url_itself_is_checked_even_with_a_test_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _production(
            monkeypatch,
            DATABASE_URL="sqlite+aiosqlite:///x.db",
            TEST_DATABASE_URL=POSTGRES_URL,
        )
        with pytest.raises(ValidationError, match="SQLite"):
            Settings()

    def test_the_refusal_does_not_echo_the_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        text = self._refused_url(monkeypatch, f"sqlite:///{FAKE_DB_PASSWORD}.db")
        assert FAKE_DB_PASSWORD not in text

    @staticmethod
    def _refused_url(monkeypatch: pytest.MonkeyPatch, url: str) -> str:
        return _refused(monkeypatch, "DATABASE_URL", DATABASE_URL=url)


class TestCors:
    def test_credentials_with_empty_origins_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _refused(monkeypatch, "CORS_ORIGINS", CORS_ALLOW_CREDENTIALS="true")

    def test_credentials_with_a_star_origin_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _refused(
            monkeypatch,
            "CORS_ORIGINS",
            CORS_ALLOW_CREDENTIALS="true",
            CORS_ORIGINS="https://a.example.test, *",
        )

    def test_star_without_credentials_is_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _production(monkeypatch, CORS_ORIGINS="*")
        assert Settings().CORS_ORIGINS == ["*"]

    def test_credentials_with_explicit_origins_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _production(
            monkeypatch,
            CORS_ALLOW_CREDENTIALS="true",
            CORS_ORIGINS="https://a.example.test, http://b.example.test:8080",
        )
        assert Settings().CORS_ALLOW_CREDENTIALS

    @pytest.mark.parametrize(
        "origin",
        ["https://a.example.test/", "https://a.example.test/app", "a.example.test", "https://"],
    )
    def test_an_origin_that_is_not_scheme_host_port_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, origin: str
    ) -> None:
        _refused(monkeypatch, "CORS_ORIGINS", CORS_ORIGINS=origin)

    def test_outside_production_a_star_with_credentials_is_stripped_with_a_warning(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("CORS_ALLOW_CREDENTIALS", "true")
        monkeypatch.setenv("CORS_ORIGINS", "*, https://a.example.test")
        # Alembic's fileConfig (run by other tests' startup) can leave app loggers disabled.
        monkeypatch.setattr(logging.getLogger("your_service_name.backend.main"), "disabled", False)
        with caplog.at_level(logging.WARNING):
            app = create_app()
        assert any("'*'" in r.getMessage() for r in caplog.records)
        assert app.state.settings.CORS_ORIGINS == ["*", "https://a.example.test"]


class TestAuth:
    def test_anonymous_mode_needs_an_explicit_opt_in(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _refused(monkeypatch, "AUTH_MODE", "ALLOW_ANONYMOUS", AUTH_MODE="none")

    def test_unset_mode_counts_as_anonymous(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _production(monkeypatch)
        monkeypatch.delenv("AUTH_MODE")
        with pytest.raises(ValidationError, match="ALLOW_ANONYMOUS"):
            Settings()

    def test_anonymous_mode_with_the_opt_in_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _production(monkeypatch, AUTH_MODE="none", ALLOW_ANONYMOUS="true")
        assert Settings().AUTH_MODE == "none"

    def test_the_opt_in_does_not_loosen_other_modes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _production(monkeypatch, AUTH_MODE="bearer", ALLOW_ANONYMOUS="true")
        with pytest.raises(ValidationError, match="AUTH_JWT_SECRET"):
            Settings()


class TestDocs:
    def test_docs_are_off_by_default_in_production(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _production(monkeypatch)
        s = Settings()
        assert s.docs_url is None
        assert s.openapi_url is None

    @pytest.mark.parametrize("name", ["API_DOCS_URL", "OPENAPI_URL"])
    def test_turning_either_on_without_the_flag_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, name: str
    ) -> None:
        text = _refused(monkeypatch, name, "ALLOW_PRODUCTION_DOCS", **{name: "/x"})
        assert "ALLOW_PRODUCTION_DOCS=true" in text

    def test_an_explicit_empty_value_is_off_and_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _production(monkeypatch, API_DOCS_URL="", OPENAPI_URL="")
        s = Settings()
        assert (s.docs_url, s.openapi_url) == (None, None)

    def test_the_flag_turns_both_on_with_their_default_paths(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _production(monkeypatch, ALLOW_PRODUCTION_DOCS="true")
        s = Settings()
        assert (s.docs_url, s.openapi_url) == ("/docs", "/openapi.json")

    def test_the_flag_allows_custom_paths(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _production(
            monkeypatch,
            ALLOW_PRODUCTION_DOCS="true",
            API_DOCS_URL="/internal/docs",
            OPENAPI_URL="/internal/openapi.json",
        )
        s = Settings()
        assert (s.docs_url, s.openapi_url) == ("/internal/docs", "/internal/openapi.json")

    def test_the_served_app_has_no_docs_routes_in_production(self) -> None:
        app = create_app(
            Settings(
                ENV="production",
                DATABASE_URL=SecretStr(POSTGRES_URL),
                DB_SSLMODE="verify-full",
                AUTH_MODE="gateway",
                ALLOWED_HOSTS=["api.example.com"],
            )
        )
        paths = {getattr(route, "path", "") for route in app.routes}
        assert "/docs" not in paths
        assert "/openapi.json" not in paths

    def test_the_served_app_has_docs_routes_with_the_flag(self) -> None:
        app = create_app(
            Settings(
                ENV="production",
                DATABASE_URL=SecretStr(POSTGRES_URL),
                DB_SSLMODE="verify-full",
                AUTH_MODE="gateway",
                ALLOWED_HOSTS=["api.example.com"],
                ALLOW_PRODUCTION_DOCS=True,
            )
        )
        # No lifespan (no `with`): only the routing is under test, not the database.
        client = TestClient(app)
        assert client.get("/openapi.json", headers={"Host": "api.example.com"}).status_code == 200  # noqa: PLR2004


class TestFailsLoudAtAppCreation:
    def test_every_problem_is_listed_in_one_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("CORS_ALLOW_CREDENTIALS", "true")
        monkeypatch.setenv("API_DOCS_URL", "/docs")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{FAKE_DB_PASSWORD}")
        with pytest.raises(ValidationError) as excinfo:
            create_app()
        text = str(excinfo.value)
        for name in (
            "DATABASE_URL",
            "CORS_ORIGINS",
            "AUTH_MODE",
            "ALLOW_ANONYMOUS",
            "API_DOCS_URL",
            "ALLOW_PRODUCTION_DOCS",
        ):
            assert name in text
        assert FAKE_DB_PASSWORD not in text
