# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The app engine's connect arguments (PostgreSQL only) and the TLS context per mode."""

from __future__ import annotations

import ssl
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from your_service_name.backend.db.connect import ssl_context
from your_service_name.backend.db.engine import _engine_kwargs
from your_service_name.backend.settings import Settings

PG_URL = "postgresql+asyncpg://app:fake@db.example.test:5432/app"
SQLITE_URL = "sqlite+aiosqlite:///x.db"
# A throwaway, public, self-signed certificate (no key): only used to load a CA file.
CA_PEM = """-----BEGIN CERTIFICATE-----
MIIBlDCCATugAwIBAgIUEHDFnYGfDyWx31MwcM02eRvAQuQwCgYIKoZIzj0EAwIw
HzEdMBsGA1UEAwwUdGVzdC1jYS5leGFtcGxlLnRlc3QwIBcNMjYxMTIxMDk1NTU4
WhgPMjEyNjEwMjgwOTU1NThaMB8xHTAbBgNVBAMMFHRlc3QtY2EuZXhhbXBsZS50
ZXN0MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEbewKDc6TjLbqT1s+yhuyLAZM
3TjMD3Cu9XjV/eSau4Fov/ro/xWFOpFBWeTcWLCfavOCy/dfCWWPpUekDLE+7qNT
MFEwHQYDVR0OBBYEFAQvpG+lKBoe3irW74iZ3UYV7C3JMB8GA1UdIwQYMBaAFAQv
pG+lKBoe3irW74iZ3UYV7C3JMA8GA1UdEwEB/wQFMAMBAf8wCgYIKoZIzj0EAwID
RwAwRAIgWdf16A6ZBBvp2OmNNC/06rvHcLhI7lzD9VYKgAS80uICIBMEdwYVVH6q
deBKf81Wz2IyGvgQB6uVaujlH0FsglyS
-----END CERTIFICATE-----
"""


def _s(**values: Any) -> Settings:  # noqa: ANN401
    """Settings from keyword values; typed loosely so a test can pass a mode it parametrizes."""
    return Settings(**values)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def ca_file(tmp_path: Path) -> str:
    path = tmp_path / "ca.pem"
    path.write_text(CA_PEM, encoding="utf-8")
    return str(path)


def test_postgresql_engine_gets_timeouts_and_tls_connect_args() -> None:
    settings = _s(
        DB_CONNECT_TIMEOUT_SECONDS=7,
        DB_STATEMENT_TIMEOUT_MS=1234,
        DB_IDLE_IN_TRANSACTION_TIMEOUT_MS=5678,
    )
    args = _engine_kwargs(PG_URL, settings)["connect_args"]
    assert args["timeout"] == 7  # noqa: PLR2004
    assert args["server_settings"] == {
        "statement_timeout": "1234",
        "idle_in_transaction_session_timeout": "5678",
    }
    assert args["ssl"] is False  # DB_SSLMODE defaults to disable: asyncpg must not "prefer"


def test_the_default_timeouts() -> None:
    settings = _s()
    assert settings.DB_CONNECT_TIMEOUT_SECONDS == 10  # noqa: PLR2004
    assert settings.DB_STATEMENT_TIMEOUT_MS == 30000  # noqa: PLR2004
    assert settings.DB_IDLE_IN_TRANSACTION_TIMEOUT_MS == 60000  # noqa: PLR2004
    assert settings.DB_MIGRATION_LOCK_TIMEOUT_MS == 10000  # noqa: PLR2004


def test_sqlite_gets_no_connect_args() -> None:
    assert "connect_args" not in _engine_kwargs(SQLITE_URL, _s())


def test_disable_means_no_tls_at_all() -> None:
    assert ssl_context(_s(DB_SSLMODE="disable")) is False


def test_require_encrypts_without_verifying() -> None:
    ctx = ssl_context(_s(DB_SSLMODE="require"))
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False


def test_verify_ca_verifies_the_chain_but_not_the_hostname(ca_file: str) -> None:
    ctx = ssl_context(_s(DB_SSLMODE="verify-ca", DB_SSLROOTCERT=ca_file))
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is False
    assert len(ctx.get_ca_certs()) == 1  # only the given CA, not the system store


def test_verify_full_verifies_chain_and_hostname(ca_file: str) -> None:
    ctx = ssl_context(_s(DB_SSLMODE="verify-full", DB_SSLROOTCERT=ca_file))
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


@pytest.mark.parametrize("mode", ["verify-ca", "verify-full"])
def test_a_verify_mode_without_a_ca_file_uses_the_system_trust_store(mode: str) -> None:
    ctx = ssl_context(_s(DB_SSLMODE=mode))
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is (mode == "verify-full")


@pytest.mark.parametrize("mode", ["require", "verify-ca", "verify-full"])
def test_tls_is_at_least_1_2(mode: str) -> None:
    ctx = ssl_context(_s(DB_SSLMODE=mode))
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_a_missing_ca_file_fails_with_a_clear_error(tmp_path: Path) -> None:
    settings = _s(DB_SSLMODE="verify-full", DB_SSLROOTCERT=str(tmp_path / "nope.pem"))
    with pytest.raises(FileNotFoundError):
        ssl_context(settings)


def test_settings_reject_an_unknown_sslmode() -> None:
    with pytest.raises(ValueError, match="DB_SSLMODE"):
        _s(DB_SSLMODE="prefer")


def test_sslmode_is_case_and_blank_tolerant(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _s(DB_SSLMODE=" Verify-Full ").DB_SSLMODE == "verify-full"
    monkeypatch.setenv("DB_SSLMODE", "")
    assert _s().DB_SSLMODE == "disable"


@pytest.mark.parametrize("name", ["DB_CONNECT_TIMEOUT_SECONDS", "DB_STATEMENT_TIMEOUT_MS"])
def test_a_non_positive_timeout_falls_back_to_the_default(name: str) -> None:
    default = Settings.model_fields[name].default
    assert getattr(_s(**{name: 0}), name) == default


class TestProductionTlsRule:
    @pytest.mark.parametrize("mode", ["disable", "require"])
    def test_a_remote_database_needs_verified_tls(self, mode: str) -> None:
        with pytest.raises(ValueError, match="DB_SSLMODE"):
            _production(mode, "db.example.test")

    @pytest.mark.parametrize("mode", ["verify-ca", "verify-full"])
    def test_a_remote_database_with_verification_is_accepted(self, mode: str) -> None:
        assert _production(mode, "db.example.test").DB_SSLMODE.startswith("verify")

    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "[::1]"])
    def test_a_loopback_host_may_disable_tls(self, host: str) -> None:
        assert _production("disable", host).DB_SSLMODE == "disable"

    @pytest.mark.parametrize(
        "tail",
        [
            "@/app?host=db.internal.example",
            "@/app?host=127.0.0.1&host=db.internal.example",
            "@/app",
            "@:5432/app",
        ],
    )
    @pytest.mark.parametrize("mode", ["disable", "require"])
    def test_a_url_hiding_the_host_fails_closed(self, mode: str, tail: str) -> None:
        with pytest.raises(ValueError, match="DB_SSLMODE"):
            _production_url(mode, f"postgresql+asyncpg://app:fake{tail}")

    @pytest.mark.parametrize(
        "tail",
        [
            "@/app?host=/cloudsql/proj:region:inst",
            "@/app?host=127.0.0.1",
            "@/app?host=localhost&host=/var/run/db",
        ],
    )
    def test_a_query_host_that_is_a_socket_or_loopback_is_allowed(self, tail: str) -> None:
        assert _production_url("disable", f"postgresql+asyncpg://app:fake{tail}")

    def test_outside_production_disable_is_fine(self) -> None:
        settings = _s(DATABASE_URL=SecretStr(PG_URL), DB_SSLMODE="disable")
        assert settings.DB_SSLMODE == "disable"


def _production(mode: str, host: str) -> Settings:
    return _s(
        ENV="production",
        DATABASE_URL=SecretStr(f"postgresql+asyncpg://app:fake@{host}:5432/app"),
        AUTH_MODE="gateway",
        ALLOWED_HOSTS=["api.example.com"],
        DB_SSLMODE=mode,
    )


def _production_url(mode: str, url: str) -> Settings:
    return _s(
        ENV="production",
        DATABASE_URL=SecretStr(url),
        AUTH_MODE="gateway",
        ALLOWED_HOSTS=["api.example.com"],
        DB_SSLMODE=mode,
    )


@pytest.mark.parametrize("query", ["ssl=require", "sslmode=verify-full", "ssl=true&x=1"])
@pytest.mark.parametrize("env", ["dev", "production"])
def test_tls_in_the_database_url_conflicts_with_db_sslmode(env: str, query: str) -> None:
    url = f"postgresql+asyncpg://app:fake@127.0.0.1:5432/app?{query}"
    with pytest.raises(ValueError, match="DB_SSLMODE") as excinfo:
        _s(
            ENV=env,
            DATABASE_URL=SecretStr(url),
            AUTH_MODE="gateway",
            ALLOWED_HOSTS=["api.example.com"],
        )
    assert "fake" not in str(excinfo.value)
