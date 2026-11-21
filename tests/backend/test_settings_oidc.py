# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Startup validation of the ``oidc`` auth mode and the shared JWKS settings."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from your_service_name.backend.settings import Settings

POSTGRES_URL = "postgresql+asyncpg://app:fake@db.example.test:5432/app"
ISSUER = "https://idp.example.com"
DEFAULT_SKEW = 60


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)


def _oidc(**over: Any) -> Settings:  # noqa: ANN401
    base: dict[str, Any] = {"AUTH_MODE": "oidc", "OIDC_ISSUER": ISSUER, "OIDC_AUDIENCE": "api"}
    return Settings(**{**base, **over})


def _prod(**over: Any) -> Settings:  # noqa: ANN401
    return _oidc(
        ENV="production", DATABASE_URL=POSTGRES_URL, ALLOWED_HOSTS="api.example.com", **over
    )


def test_defaults() -> None:
    s = _oidc()
    assert s.OIDC_ALGORITHMS == ["RS256", "ES256"]
    assert s.OIDC_CLOCK_SKEW_SECONDS == DEFAULT_SKEW
    assert s.OIDC_USER_ID_CLAIM == "sub"
    assert s.OIDC_ROLES_CLAIM is None
    assert (s.JWKS_CACHE_TTL_SECONDS, s.JWKS_MIN_REFETCH_SECONDS, s.JWKS_MAX_STALE_SECONDS) == (
        3600,
        30,
        86400,
    )


def test_audience_is_comma_separated() -> None:
    assert _oidc(OIDC_AUDIENCE="a, b").OIDC_AUDIENCE == ["a", "b"]


@pytest.mark.parametrize("missing", ["OIDC_ISSUER", "OIDC_AUDIENCE"])
def test_required_fields(missing: str) -> None:
    with pytest.raises(ValidationError, match=missing):
        _oidc(**{missing: ""})


@pytest.mark.parametrize("algs", ["none", "HS256", "RS256,HS512", "RS256,none", "XX999", "NONE"])
def test_unsafe_algorithms_are_refused(algs: str) -> None:
    with pytest.raises(ValidationError, match="OIDC_ALGORITHMS"):
        _oidc(OIDC_ALGORITHMS=algs)


def test_algorithms_are_normalised() -> None:
    assert _oidc(OIDC_ALGORITHMS="rs256, ps256").OIDC_ALGORITHMS == ["RS256", "PS256"]


@pytest.mark.parametrize("skew", [-1, 301])
def test_skew_is_bounded(skew: int) -> None:
    with pytest.raises(ValidationError, match="OIDC_CLOCK_SKEW_SECONDS"):
        _oidc(OIDC_CLOCK_SKEW_SECONDS=skew)


def test_jwks_url_https_only_in_production() -> None:
    with pytest.raises(ValidationError, match="OIDC_JWKS_URL"):
        _prod(OIDC_JWKS_URL="http://localhost:9000/jwks")
    assert _prod(OIDC_JWKS_URL="https://idp.example.com/jwks").AUTH_MODE == "oidc"


def test_http_is_allowed_for_localhost_outside_production() -> None:
    assert _oidc(OIDC_JWKS_URL="http://localhost:9000/jwks").OIDC_JWKS_URL
    assert _oidc(OIDC_JWKS_URL="http://127.0.0.1:9000/jwks").OIDC_JWKS_URL


def test_http_is_refused_for_other_hosts_outside_production() -> None:
    with pytest.raises(ValidationError, match="OIDC_JWKS_URL"):
        _oidc(OIDC_JWKS_URL="http://idp.example.com/jwks")


def test_discovery_needs_an_https_issuer_in_production() -> None:
    with pytest.raises(ValidationError, match="OIDC_ISSUER"):
        _prod(OIDC_ISSUER="http://idp.example.com")


def test_production_accepts_oidc_without_allow_anonymous() -> None:
    assert _prod().AUTH_MODE == "oidc"


def test_the_error_does_not_echo_the_url() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _oidc(OIDC_JWKS_URL="http://idp.example.com/jwks?token=hunter2")
    assert "hunter2" not in str(excinfo.value)


# --- cloudflare-access --------------------------------------------------------


def _cf(**over: Any) -> Settings:  # noqa: ANN401
    base: dict[str, Any] = {
        "AUTH_MODE": "cloudflare-access",
        "CF_ACCESS_TEAM_DOMAIN": "team.example.com",
        "CF_ACCESS_AUD": "abc123",
    }
    return Settings(**{**base, **over})


@pytest.mark.parametrize("missing", ["CF_ACCESS_TEAM_DOMAIN", "CF_ACCESS_AUD"])
def test_cf_required_fields(missing: str) -> None:
    with pytest.raises(ValidationError, match=missing):
        _cf(**{missing: ""})


@pytest.mark.parametrize(
    "domain",
    ["https://team.example.com", "team.example.com/path", "team.example.com:8443", "team", "a b.c"],
)
def test_cf_team_domain_must_be_a_bare_hostname(domain: str) -> None:
    with pytest.raises(ValidationError, match="CF_ACCESS_TEAM_DOMAIN"):
        _cf(CF_ACCESS_TEAM_DOMAIN=domain)


def test_cf_team_domain_is_lowercased() -> None:
    assert _cf(CF_ACCESS_TEAM_DOMAIN="Team.Example.com").CF_ACCESS_TEAM_DOMAIN == "team.example.com"


def test_cf_production_accepts_the_mode_without_allow_anonymous() -> None:
    s = _cf(ENV="production", DATABASE_URL=POSTGRES_URL, ALLOWED_HOSTS="api.example.com")
    assert s.AUTH_MODE == "cloudflare-access"
