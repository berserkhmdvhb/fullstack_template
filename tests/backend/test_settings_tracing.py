# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Settings for opt-in tracing: defaults, the required endpoint, ranges, the secret header."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from your_service_name.backend.settings import DEFAULT_SERVICE_NAME, Settings, parse_header_list

FAKE_HEADER = "Bearer fake-collector-token-not-real"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_off() -> None:
    s = Settings()
    assert s.TRACING_ENABLED is False
    assert s.OTEL_EXPORTER_OTLP_ENDPOINT is None
    assert s.OTEL_EXPORTER_OTLP_HEADERS is None
    assert s.TRACING_SAMPLE_RATIO == 1.0
    assert s.OTEL_SERVICE_NAME == DEFAULT_SERVICE_NAME == "your_service_name"


def test_enabled_needs_an_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACING_ENABLED", "true")
    with pytest.raises(ValidationError, match="OTEL_EXPORTER_OTLP_ENDPOINT is required"):
        Settings()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318/")
    assert Settings().OTEL_EXPORTER_OTLP_ENDPOINT == "http://collector:4318"


def test_blank_endpoint_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    assert Settings().OTEL_EXPORTER_OTLP_ENDPOINT is None


@pytest.mark.parametrize("value", ["collector:4318", "ftp://collector", "http://", "grpc://c:4317"])
def test_endpoint_must_be_an_http_url(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", value)
    with pytest.raises(ValidationError, match="http"):
        Settings()


@pytest.mark.parametrize("value", ["-0.1", "1.5", "abc"])
def test_sample_ratio_is_zero_to_one(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("TRACING_SAMPLE_RATIO", value)
    with pytest.raises(ValidationError, match="TRACING_SAMPLE_RATIO"):
        Settings()


def test_headers_are_secret_and_never_echoed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", f"authorization={FAKE_HEADER}")
    s = Settings()
    assert FAKE_HEADER not in repr(s)
    assert FAKE_HEADER not in str(s.model_dump())
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", FAKE_HEADER)  # no `=`
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert FAKE_HEADER not in str(caught.value)


def test_parse_header_list() -> None:
    assert parse_header_list("a=b, c = d=e ,") == {"a": "b", "c": "d=e"}
    with pytest.raises(ValueError, match="name=value"):
        parse_header_list("=x")


PROD = {
    "ENV": "production",
    "DATABASE_URL": "postgresql+asyncpg://u:p@db.example:5432/app",
    "DB_SSLMODE": "verify-full",
    "ALLOWED_HOSTS": "api.example.com",
    "AUTH_MODE": "gateway",
    "TRACING_ENABLED": "true",
}


@pytest.mark.parametrize(
    "endpoint", ["http://collector.monitoring.svc:4318", "http://10.0.0.5:4318"]
)
def test_production_refuses_headers_to_a_plaintext_remote_collector(
    monkeypatch: pytest.MonkeyPatch, endpoint: str
) -> None:
    for name, value in PROD.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", endpoint)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", f"authorization={FAKE_HEADER}")
    with pytest.raises(ValidationError, match="OTEL_EXPORTER_OTLP_HEADERS") as caught:
        Settings()
    assert FAKE_HEADER not in str(caught.value)


@pytest.mark.parametrize(
    ("endpoint", "headers"),
    [
        ("https://collector.example.com:4318", "authorization=x"),
        ("http://127.0.0.1:4318", "authorization=x"),
        ("http://localhost:4318", "authorization=x"),
        ("http://collector.monitoring.svc:4318", None),
    ],
)
def test_production_allows_tls_loopback_or_no_headers(
    monkeypatch: pytest.MonkeyPatch, endpoint: str, headers: str | None
) -> None:
    for name, value in PROD.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", endpoint)
    if headers:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", headers)
    Settings()


def test_outside_production_headers_over_http_are_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACING_ENABLED", "true")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "a=b")
    Settings()


@pytest.mark.parametrize(
    ("name", "use_instead"),
    [
        ("OTEL_EXPORTER_OTLP_TRACES_HEADERS", "OTEL_EXPORTER_OTLP_HEADERS"),
        ("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT"),
    ],
)
@pytest.mark.parametrize("env", ["dev", "production"])
def test_the_per_signal_variables_are_refused(
    monkeypatch: pytest.MonkeyPatch, name: str, use_instead: str, env: str
) -> None:
    monkeypatch.setenv("ENV", env)
    if env == "production":
        for key, value in PROD.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("TRACING_ENABLED", "false")
    monkeypatch.setenv(name, f"authorization={FAKE_HEADER}")
    with pytest.raises(ValidationError, match=use_instead) as caught:
        Settings()
    assert name in str(caught.value)
    assert FAKE_HEADER not in str(caught.value)
