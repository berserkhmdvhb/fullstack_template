# your-service-name — adaptable service template (see wiki/getting-started.md)
"""RATE_LIMIT_STORAGE: schemes, the secret URL, the timeout and the production plaintext rule."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from your_service_name.backend.settings import Settings

PASSWORD = "fake-redis-password-not-real"  # noqa: S105
PROD = {
    "ENV": "production",
    "DATABASE_URL": "postgresql+asyncpg://u:p@db.example:5432/app",
    "DB_SSLMODE": "verify-full",
    "ALLOWED_HOSTS": "api.example.com",
    "AUTH_MODE": "gateway",
    "AUTH_PROXY_CIDRS": "10.20.0.0/16",
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)


def _set(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def test_defaults() -> None:
    s = Settings()
    assert s.RATE_LIMIT_STORAGE.get_secret_value() == "memory://"
    assert s.RATE_LIMIT_STORAGE_TIMEOUT_SECONDS == 0.2  # noqa: PLR2004


def test_blank_storage_is_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, RATE_LIMIT_STORAGE="")
    assert Settings().RATE_LIMIT_STORAGE.get_secret_value() == "memory://"


@pytest.mark.parametrize(
    "url",
    [
        "memory://",
        f"redis://:{PASSWORD}@cache.internal:6379/0",
        f"rediss://:{PASSWORD}@cache.example.com:6380",
        "redis+unix:///var/run/redis.sock?db=0",
    ],
)
def test_every_supported_scheme_is_accepted(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    _set(monkeypatch, RATE_LIMIT_STORAGE=url)
    assert Settings().RATE_LIMIT_STORAGE.get_secret_value() == url


@pytest.mark.parametrize(
    "url",
    ["memcached://h:11211", "mongodb://h", "http://h", "async+redis://h", "redis", "redis://"],
)
def test_other_values_are_refused_and_never_echoed(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    _set(monkeypatch, RATE_LIMIT_STORAGE=url + f"/{PASSWORD}")
    with pytest.raises(ValidationError, match="RATE_LIMIT_STORAGE") as caught:
        Settings()
    assert PASSWORD not in str(caught.value)


def test_the_url_is_never_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, RATE_LIMIT_STORAGE=f"redis://:{PASSWORD}@cache:6379")
    assert PASSWORD not in repr(Settings())


@pytest.mark.parametrize("value", ["0", "-1", "11", "abc"])
def test_timeout_range(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    _set(monkeypatch, RATE_LIMIT_STORAGE_TIMEOUT_SECONDS=value)
    with pytest.raises(ValidationError, match="RATE_LIMIT_STORAGE_TIMEOUT_SECONDS"):
        Settings()


@pytest.mark.parametrize(
    "url",
    [
        f"redis://:{PASSWORD}@cache.internal:6379/0",
        f"redis://:{PASSWORD}@10.0.0.5:6379",
        "redis://",
    ],
)
def test_production_refuses_plaintext_to_a_remote_host(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    _set(monkeypatch, **PROD, RATE_LIMIT_STORAGE=url)
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert "RATE_LIMIT_STORAGE" in str(caught.value) or "RATE_LIMIT" in str(caught.value)
    assert PASSWORD not in str(caught.value)


@pytest.mark.parametrize(
    "url",
    [
        "memory://",
        f"rediss://:{PASSWORD}@cache.example.com:6380",
        "redis+unix:///var/run/redis.sock",
        "redis://127.0.0.1:6379",
        "redis://localhost:6379/1",
        "redis://[::1]:6379",
    ],
)
def test_production_accepts_tls_unix_loopback_and_memory(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    _set(monkeypatch, **PROD, RATE_LIMIT_STORAGE=url)
    assert Settings().RATE_LIMIT_STORAGE.get_secret_value() == url


def test_outside_production_plaintext_remote_is_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, RATE_LIMIT_STORAGE="redis://cache.internal:6379")
    Settings()


@pytest.mark.parametrize(
    "url",
    [
        f"rediss://:{PASSWORD}@cache.example:6379x",
        f"rediss://:{PASSWORD}@cache.example:70000",
        f"redis://:{PASSWORD}@localhost/0",
        f"rediss://:{PASSWORD}@cache.example",
        f"redis+unix://:{PASSWORD}@cache.example/var/run/redis.sock",
    ],
)
def test_urls_the_storage_cannot_parse_are_refused_without_the_password(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    _set(monkeypatch, RATE_LIMIT_STORAGE=url)
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert "RATE_LIMIT_STORAGE" in str(caught.value)
    assert PASSWORD not in str(caught.value)


def test_a_missing_port_says_to_add_one(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, RATE_LIMIT_STORAGE="redis://localhost/0")
    with pytest.raises(ValidationError, match=r":6379"):
        Settings()


@pytest.mark.parametrize(
    "query", ["ssl_cert_reqs=none", "ssl_cert_reqs=optional", "ssl_check_hostname=false"]
)
def test_production_refuses_unverified_tls(monkeypatch: pytest.MonkeyPatch, query: str) -> None:
    _set(monkeypatch, **PROD, RATE_LIMIT_STORAGE=f"rediss://cache.example.com:6380/0?{query}")
    with pytest.raises(ValidationError, match="RATE_LIMIT_STORAGE"):
        Settings()


@pytest.mark.parametrize(
    "query", ["ssl_cert_reqs=required", "ssl_ca_certs=/etc/ca.pem", "ssl_check_hostname=true"]
)
def test_production_allows_verified_tls_options(
    monkeypatch: pytest.MonkeyPatch, query: str
) -> None:
    _set(monkeypatch, **PROD, RATE_LIMIT_STORAGE=f"rediss://cache.example.com:6380/0?{query}")
    Settings()


def test_outside_production_unverified_tls_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, RATE_LIMIT_STORAGE="rediss://cache.example.com:6380/0?ssl_cert_reqs=none")
    Settings()
