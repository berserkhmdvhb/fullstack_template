# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Unit tests for the typed ``Settings`` class: parsing, defaults, auth modes, secrets."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from your_service_name.backend.settings import Settings

# Obviously fake; never a real credential.
FAKE_SECRET = "fake-secret-value-not-real"  # noqa: S105
FAKE_DB_PASSWORD = "fake-db-password-not-real"  # noqa: S105
DEFAULT_POOL, DEFAULT_OVERFLOW = 10, 20


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every Settings field starts unset, so a test sees only the defaults it asks about."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)


class TestDefaults:
    """With no environment, Settings keeps today's behaviour."""

    def test_defaults(self) -> None:
        s = Settings()
        assert s.ENV == "dev"
        assert s.AUTH_MODE == "none"
        assert s.API_VERSION == "v1"
        assert s.API_TITLE == "Backend Title"
        assert s.RATE_LIMIT_DEFAULT == "100/minute"
        assert s.LOG_REQUESTS is False
        assert s.CORS_ORIGINS == []
        assert s.CORS_ALLOW_CREDENTIALS is False
        assert s.DATABASE_URL is None
        assert s.SQLITE_PATH == "data/items.db"
        assert s.DB_POOL_SIZE == DEFAULT_POOL
        assert s.DB_MAX_OVERFLOW == DEFAULT_OVERFLOW
        assert s.AUTH_JWT_ALGORITHM == "HS256"
        assert s.AUTH_JWT_USER_ID_CLAIM == "sub"

    def test_names_are_case_sensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("auth_mode", "gateway")
        assert Settings().AUTH_MODE == "none"

    def test_unrelated_variables_are_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BACKEND_PORT", "8000")
        Settings()


class TestParsing:
    """Env strings are parsed the way the old per-variable reads did."""

    def test_csv_lists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CORS_ORIGINS", " https://a.test , https://b.test ,")
        monkeypatch.setenv("CORS_ALLOW_METHODS", "GET,POST")
        assert Settings().CORS_ORIGINS == ["https://a.test", "https://b.test"]
        assert Settings().CORS_ALLOW_METHODS == ["GET", "POST"]

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("", []),
            ("https://a.com,https://b.com", ["https://a.com", "https://b.com"]),
            ("https://a.com,, https://b.com", ["https://a.com", "https://b.com"]),
        ],
    )
    def test_origins_csv(
        self, monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[str]
    ) -> None:
        monkeypatch.setenv("CORS_ORIGINS", raw)
        assert expected == Settings().CORS_ORIGINS

    def test_blank_csv_methods_and_headers_use_defaults(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CORS_ALLOW_METHODS", "  ")
        monkeypatch.setenv("CORS_ALLOW_HEADERS", "")
        s = Settings()
        assert "DELETE" in s.CORS_ALLOW_METHODS
        assert "Authorization" in s.CORS_ALLOW_HEADERS

    def test_blank_values_fall_back_to_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in (
            "AUTH_MODE",
            "AUTH_USER_ID_HEADER",
            "LOG_REQUESTS",
            "DB_POOL_SIZE",
            "API_VERSION",
        ):
            monkeypatch.setenv(name, "")
        s = Settings()
        assert s.AUTH_MODE == "none"
        assert s.AUTH_USER_ID_HEADER == "x-authenticated-user-id"
        assert s.LOG_REQUESTS is False
        assert s.DB_POOL_SIZE == DEFAULT_POOL
        assert s.API_VERSION == "v1"

    def test_blank_docs_url_stays_blank(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """API_DOCS_URL="" disables the docs (the documented switch), so it is not defaulted."""
        monkeypatch.setenv("API_DOCS_URL", "")
        assert Settings().API_DOCS_URL == ""

    def test_bad_pool_size_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DB_POOL_SIZE", "lots")
        monkeypatch.setenv("DB_MAX_OVERFLOW", "-3")
        s = Settings()
        assert (s.DB_POOL_SIZE, s.DB_MAX_OVERFLOW) == (DEFAULT_POOL, DEFAULT_OVERFLOW)

    def test_auth_mode_is_normalised(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTH_MODE", " Gateway ")
        assert Settings().AUTH_MODE == "gateway"

    def test_unsupported_api_version_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("API_VERSION", "v99")
        with pytest.raises(ValidationError, match="API_VERSION"):
            Settings()

    def test_api_version_is_trimmed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("API_VERSION", "  v1  ")
        assert Settings().API_VERSION == "v1"

    def test_bad_rate_limit_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RATE_LIMIT_DEFAULT", "often")
        with pytest.raises(ValidationError, match="RATE_LIMIT_DEFAULT"):
            Settings()

    def test_bad_bool_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CORS_ALLOW_CREDENTIALS", "banana")
        with pytest.raises(ValidationError, match="CORS_ALLOW_CREDENTIALS"):
            Settings()


class TestAuthModes:
    """Each AUTH_MODE validates what it needs at startup, not at the first request."""

    def test_unknown_mode_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTH_MODE", "ldap")
        with pytest.raises(ValidationError, match="AUTH_MODE"):
            Settings()

    def test_bearer_needs_a_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTH_MODE", "bearer")
        with pytest.raises(ValidationError, match="AUTH_JWT_SECRET"):
            Settings()

    def test_bearer_with_secret_is_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTH_MODE", "bearer")
        monkeypatch.setenv("AUTH_JWT_SECRET", FAKE_SECRET)
        assert Settings().AUTH_MODE == "bearer"

    def test_bearer_refuses_a_non_hmac_algorithm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTH_MODE", "bearer")
        monkeypatch.setenv("AUTH_JWT_SECRET", FAKE_SECRET)
        monkeypatch.setenv("AUTH_JWT_ALGORITHM", "none")
        with pytest.raises(ValidationError, match="AUTH_JWT_ALGORITHM"):
            Settings()

    def test_algorithm_is_upper_cased(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTH_JWT_ALGORITHM", "hs512")
        assert Settings().AUTH_JWT_ALGORITHM == "HS512"

    def test_gateway_needs_nothing_else(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTH_MODE", "gateway")
        assert Settings().AUTH_USER_ID_HEADER == "x-authenticated-user-id"

    def test_every_invalid_field_is_listed_in_one_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUTH_MODE", "ldap")
        monkeypatch.setenv("RATE_LIMIT_DEFAULT", "often")
        monkeypatch.setenv("API_VERSION", "v99")
        with pytest.raises(ValidationError) as excinfo:
            Settings()
        text = str(excinfo.value)
        for name in ("AUTH_MODE", "RATE_LIMIT_DEFAULT", "API_VERSION"):
            assert name in text

    def test_bearer_problems_are_listed_together(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTH_MODE", "bearer")
        monkeypatch.setenv("AUTH_JWT_ALGORITHM", "none")
        with pytest.raises(ValidationError) as excinfo:
            Settings()
        text = str(excinfo.value)
        assert "AUTH_JWT_SECRET" in text
        assert "AUTH_JWT_ALGORITHM" in text


class TestSecretsNeverEcho:
    """A secret-bearing value must not leak through repr, str or an error message."""

    def test_repr_and_str_hide_secrets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTH_MODE", "bearer")
        monkeypatch.setenv("AUTH_JWT_SECRET", FAKE_SECRET)
        monkeypatch.setenv("DATABASE_URL", f"postgresql+asyncpg://u:{FAKE_DB_PASSWORD}@h/db")
        monkeypatch.setenv("TEST_DATABASE_URL", f"sqlite+aiosqlite:///{FAKE_DB_PASSWORD}")
        s = Settings()
        for text in (repr(s), str(s), repr(s.model_dump())):
            assert FAKE_SECRET not in text
            assert FAKE_DB_PASSWORD not in text

    def test_invalid_secret_field_value_is_not_in_the_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A bad AUTH_JWT_SECRET-adjacent configuration must not echo the secret itself."""
        monkeypatch.setenv("AUTH_MODE", "bearer")
        monkeypatch.setenv("AUTH_JWT_SECRET", FAKE_SECRET)
        monkeypatch.setenv("AUTH_JWT_ALGORITHM", "none")  # invalid -> error is raised
        with pytest.raises(ValidationError) as excinfo:
            Settings()
        assert FAKE_SECRET not in str(excinfo.value)
        assert FAKE_SECRET not in repr(excinfo.value)

    def test_wrongly_typed_secret_is_not_in_the_error(self) -> None:
        """A non-string secret fails validation; the offending input is not echoed."""
        with pytest.raises(ValidationError) as excinfo:
            Settings(DATABASE_URL=["leaky", FAKE_DB_PASSWORD])  # type: ignore[arg-type]
        assert FAKE_DB_PASSWORD not in str(excinfo.value)
