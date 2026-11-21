# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The token-verifying auth modes (``oidc``) end to end, against a local JWKS.

Every rejection is a 401 problem with a generic detail and ``WWW-Authenticate`` carrying
``error="invalid_token"``; a missing token gets a plain ``Bearer``; an unreachable key set is a 503.
Keys are generated in the session and served through ``httpx.MockTransport`` (no network).
"""

from __future__ import annotations

import base64
import hmac
import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from tests.backend.auth.conftest import FakeClock, FakeIdP, KeyPair, sign
from your_service_name.backend.auth import Principal, get_current_user
from your_service_name.backend.auth.dependencies import _jwks_cache
from your_service_name.backend.auth.jwks import JwksCache
from your_service_name.backend.errors import install_error_handling
from your_service_name.backend.settings import Settings

if TYPE_CHECKING:
    from collections.abc import Callable

ME = "/me"
ISSUER = "https://idp.example.com"
AUDIENCE = "api://your-service"
INVALID = 'Bearer error="invalid_token"'
GENERIC = "invalid or expired token"
MIN_REFETCH = 30
SKEW = 60
MAX_TOKEN_BYTES = 8192
HTTP_OK, HTTP_UNAUTHORIZED, HTTP_UNAVAILABLE = 200, 401, 503
TWICE = 2


@dataclass(frozen=True)
class ModeSpec:
    """What differs between the token modes: settings, where the token travels, the source."""

    name: str
    issuer: str
    audience: str
    source: str
    settings: Callable[[FakeIdP], dict[str, Any]]
    headers: Callable[[str], dict[str, str]]


OIDC = ModeSpec(
    name="oidc",
    issuer=ISSUER,
    audience=AUDIENCE,
    source="oidc",
    settings=lambda idp: {
        "AUTH_MODE": "oidc",
        "OIDC_ISSUER": ISSUER,
        "OIDC_AUDIENCE": AUDIENCE,
        "OIDC_JWKS_URL": idp.jwks_url,
    },
    headers=lambda token: {"Authorization": f"Bearer {token}"},
)
CF_TEAM = "team.cloudflareaccess.com"
CF_AUD = "0123456789abcdef"
CF_HEADER = "Cf-Access-Jwt-Assertion"
CF = ModeSpec(
    name="cloudflare-access",
    issuer=f"https://{CF_TEAM}",
    audience=CF_AUD,
    source="cloudflare-access",
    settings=lambda _idp: {
        "AUTH_MODE": "cloudflare-access",
        "CF_ACCESS_TEAM_DOMAIN": CF_TEAM,
        "CF_ACCESS_AUD": CF_AUD,
    },
    headers=lambda token: {CF_HEADER: token},
)
MODES = [OIDC, CF]


@pytest.fixture(params=MODES, ids=lambda m: m.name)
def mode(request: pytest.FixtureRequest) -> ModeSpec:
    spec: ModeSpec = request.param
    return spec


def _claims(mode: ModeSpec, **overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    now = int(time.time())
    base: dict[str, Any] = {
        "iss": mode.issuer,
        "aud": mode.audience,
        "sub": "user-1",
        "iat": now,
        "exp": now + 300,
    }
    merged = {**base, **overrides}
    return {k: v for k, v in merged.items() if v is not None}


def _app(mode: ModeSpec, idp: FakeIdP, clock: FakeClock, **extra: Any) -> FastAPI:  # noqa: ANN401
    app = FastAPI()
    settings = Settings(**{**mode.settings(idp), **extra})
    app.state.settings = settings
    app.state.jwks_cache = JwksCache(
        jwks_url=idp.jwks_url, client=idp.client(), clock=clock, min_refetch=MIN_REFETCH
    )
    install_error_handling(app)

    @app.get(ME)
    def me(user: Annotated[Principal, Depends(get_current_user)]) -> dict[str, Any]:
        return {"id": user.user_id, "source": user.identity_source, "roles": user.roles}

    return app


@pytest.fixture
def call(mode: ModeSpec, idp: FakeIdP, clock: FakeClock) -> Callable[..., Any]:
    """``call(token, **settings)`` -> response of GET /me with the token in the mode's place."""

    def _call(token: str | None, **extra: Any) -> Any:  # noqa: ANN401
        with TestClient(_app(mode, idp, clock, **extra)) as client:
            return client.get(ME, headers=mode.headers(token) if token else {})

    return _call


@pytest.fixture
def oidc_call(idp: FakeIdP, clock: FakeClock) -> Callable[..., Any]:
    """Like ``call`` but always in the oidc mode (for the tests of oidc-only settings)."""

    def _call(token: str | None, **extra: Any) -> Any:  # noqa: ANN401
        with TestClient(_app(OIDC, idp, clock, **extra)) as client:
            return client.get(ME, headers=OIDC.headers(token) if token else {})

    return _call


def _rejected(res: Any) -> None:  # noqa: ANN401
    assert res.status_code == HTTP_UNAUTHORIZED
    assert res.headers["www-authenticate"] == INVALID
    body = res.json()
    assert body["detail"] == GENERIC
    assert res.headers["content-type"].startswith("application/problem+json")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _forge(
    header: dict[str, Any], claims: dict[str, Any], key: bytes | None = None, alg: str = ""
) -> str:
    head = _b64(json.dumps(header).encode())
    body = _b64(json.dumps(claims).encode())
    signing_input = f"{head}.{body}".encode()
    sig = hmac.new(key, signing_input, alg).digest() if key is not None else b""
    return f"{head}.{body}.{_b64(sig)}"


# --- accepted -----------------------------------------------------------------


def test_valid_rs256(mode: ModeSpec, call: Callable[..., Any], rsa_key: KeyPair) -> None:
    res = call(sign(rsa_key, _claims(mode), alg="RS256"))
    assert res.status_code == HTTP_OK
    assert res.json() == {"id": "user-1", "source": mode.source, "roles": []}


def test_audience_list_intersects(
    mode: ModeSpec, call: Callable[..., Any], rsa_key: KeyPair
) -> None:
    token = sign(rsa_key, _claims(mode, aud=["other", mode.audience]), alg="RS256")
    assert call(token).status_code == HTTP_OK


def test_expired_within_skew_is_accepted(
    mode: ModeSpec, call: Callable[..., Any], rsa_key: KeyPair
) -> None:
    token = sign(rsa_key, _claims(mode, exp=int(time.time()) - SKEW // 2), alg="RS256")
    assert call(token).status_code == HTTP_OK


def test_nbf_within_skew_is_accepted(
    mode: ModeSpec, call: Callable[..., Any], rsa_key: KeyPair
) -> None:
    token = sign(rsa_key, _claims(mode, nbf=int(time.time()) + SKEW // 2), alg="RS256")
    assert call(token).status_code == HTTP_OK


# --- rejected: claims ---------------------------------------------------------


def test_expired(mode: ModeSpec, call: Callable[..., Any], rsa_key: KeyPair) -> None:
    _rejected(call(sign(rsa_key, _claims(mode, exp=int(time.time()) - 3600), alg="RS256")))


def test_expired_outside_skew(mode: ModeSpec, call: Callable[..., Any], rsa_key: KeyPair) -> None:
    _rejected(call(sign(rsa_key, _claims(mode, exp=int(time.time()) - 2 * SKEW), alg="RS256")))


def test_nbf_in_the_future(mode: ModeSpec, call: Callable[..., Any], rsa_key: KeyPair) -> None:
    _rejected(call(sign(rsa_key, _claims(mode, nbf=int(time.time()) + 3600), alg="RS256")))


def test_nbf_outside_skew(mode: ModeSpec, call: Callable[..., Any], rsa_key: KeyPair) -> None:
    _rejected(call(sign(rsa_key, _claims(mode, nbf=int(time.time()) + 2 * SKEW), alg="RS256")))


def test_wrong_audience(mode: ModeSpec, call: Callable[..., Any], rsa_key: KeyPair) -> None:
    _rejected(call(sign(rsa_key, _claims(mode, aud="someone-else"), alg="RS256")))


def test_wrong_issuer(mode: ModeSpec, call: Callable[..., Any], rsa_key: KeyPair) -> None:
    _rejected(call(sign(rsa_key, _claims(mode, iss="https://evil.example.com"), alg="RS256")))


def test_issuer_must_match_exactly(
    mode: ModeSpec, call: Callable[..., Any], rsa_key: KeyPair
) -> None:
    _rejected(call(sign(rsa_key, _claims(mode, iss=mode.issuer + "/"), alg="RS256")))


@pytest.mark.parametrize("claim", ["exp", "iss", "aud"])
def test_required_claim_missing(
    mode: ModeSpec, call: Callable[..., Any], rsa_key: KeyPair, claim: str
) -> None:
    _rejected(call(sign(rsa_key, _claims(mode, **{claim: None}), alg="RS256")))


@pytest.mark.parametrize("value", [None, "", 42, ["a"]])
def test_user_claim_missing_or_not_a_string(
    mode: ModeSpec,
    call: Callable[..., Any],
    rsa_key: KeyPair,
    value: Any,  # noqa: ANN401
) -> None:
    _rejected(call(sign(rsa_key, _claims(mode, sub=value), alg="RS256")))


# --- rejected: header, key, signature -----------------------------------------


def test_unknown_kid_refetches_once_then_401(
    mode: ModeSpec, idp: FakeIdP, clock: FakeClock, rsa_key: KeyPair
) -> None:
    good = sign(rsa_key, _claims(mode), alg="RS256")
    unknown = sign(rsa_key, _claims(mode), alg="RS256", headers={"kid": "unknown"})
    with TestClient(_app(mode, idp, clock)) as client:
        assert client.get(ME, headers=mode.headers(good)).status_code == HTTP_OK
        assert idp.jwks_requests == 1
        clock.advance(MIN_REFETCH + 1)
        for _ in range(5):
            _rejected(client.get(ME, headers=mode.headers(unknown)))
    assert idp.jwks_requests == TWICE


def test_missing_kid(mode: ModeSpec, call: Callable[..., Any], rsa_key: KeyPair) -> None:
    _rejected(call(sign(rsa_key, _claims(mode), alg="RS256", headers={"kid": None})))


def test_alg_none(mode: ModeSpec, call: Callable[..., Any], rsa_key: KeyPair) -> None:
    token = _forge({"alg": "none", "kid": rsa_key.kid}, _claims(mode))
    _rejected(call(token))


def test_hs256_signed_with_the_public_key_bytes(
    mode: ModeSpec, call: Callable[..., Any], rsa_key: KeyPair
) -> None:
    token = _forge(
        {"alg": "HS256", "kid": rsa_key.kid}, _claims(mode), key=rsa_key.public_pem, alg="sha256"
    )
    _rejected(call(token))


def test_rs256_token_whose_kid_is_an_ec_key(
    mode: ModeSpec, call: Callable[..., Any], rsa_key: KeyPair, ec_key: KeyPair
) -> None:
    _rejected(call(sign(rsa_key, _claims(mode), alg="RS256", headers={"kid": ec_key.kid})))


def test_signature_from_another_key(
    mode: ModeSpec, call: Callable[..., Any], rsa_key: KeyPair, rsa_key_2: KeyPair
) -> None:
    _rejected(call(sign(rsa_key_2, _claims(mode), alg="RS256", headers={"kid": rsa_key.kid})))


def test_token_over_8_kib(mode: ModeSpec, call: Callable[..., Any], rsa_key: KeyPair) -> None:
    token = sign(rsa_key, _claims(mode, pad="x" * MAX_TOKEN_BYTES), alg="RS256")
    assert len(token) > MAX_TOKEN_BYTES
    _rejected(call(token))


def test_crit_header(mode: ModeSpec, call: Callable[..., Any], rsa_key: KeyPair) -> None:
    token = sign(rsa_key, _claims(mode), alg="RS256", headers={"crit": ["exp"]})
    _rejected(call(token))


def test_garbage_token(call: Callable[..., Any]) -> None:
    _rejected(call("not-a-jwt"))


# --- missing token, outages, logging ------------------------------------------


def test_missing_token_is_a_plain_bearer_challenge(call: Callable[..., Any]) -> None:
    res = call(None)
    assert res.status_code == HTTP_UNAUTHORIZED
    assert res.headers["www-authenticate"] == "Bearer"
    assert res.headers["content-type"].startswith("application/problem+json")


def test_provider_down_is_a_503_problem(
    mode: ModeSpec, idp: FakeIdP, clock: FakeClock, rsa_key: KeyPair
) -> None:
    idp.fail = True
    token = sign(rsa_key, _claims(mode), alg="RS256")
    with TestClient(_app(mode, idp, clock)) as client:
        res = client.get(ME, headers=mode.headers(token))
    assert res.status_code == HTTP_UNAVAILABLE
    assert res.json()["detail"] == "authentication temporarily unavailable"
    assert res.headers["content-type"].startswith("application/problem+json")


def test_reason_is_logged_but_not_the_token(
    mode: ModeSpec, call: Callable[..., Any], rsa_key: KeyPair, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO")
    token = sign(rsa_key, _claims(mode, aud="someone-else"), alg="RS256")
    res = call(token)
    assert GENERIC in res.text
    assert "audience" not in res.text.lower()
    assert "audience" in caplog.text.lower()
    assert token not in caplog.text
    assert token.split(".")[1] not in caplog.text


# --- cloudflare-access only ---------------------------------------------------


def test_cf_the_authorization_header_is_not_a_token_source(
    idp: FakeIdP, clock: FakeClock, rsa_key: KeyPair
) -> None:
    token = sign(rsa_key, _claims(CF), alg="RS256")
    with TestClient(_app(CF, idp, clock)) as client:
        res = client.get(ME, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == HTTP_UNAUTHORIZED
    assert res.headers["www-authenticate"] == "Bearer"


def test_cf_the_cookie_is_ignored(idp: FakeIdP, clock: FakeClock, rsa_key: KeyPair) -> None:
    token = sign(rsa_key, _claims(CF), alg="RS256")
    with TestClient(_app(CF, idp, clock)) as client:
        client.cookies.set("CF_Authorization", token)
        res = client.get(ME)
    assert res.status_code == HTTP_UNAUTHORIZED
    assert res.headers["www-authenticate"] == "Bearer"


def test_cf_only_rs256_is_accepted(idp: FakeIdP, clock: FakeClock, ec_key: KeyPair) -> None:
    with TestClient(_app(CF, idp, clock)) as client:
        res = client.get(ME, headers=CF.headers(sign(ec_key, _claims(CF), alg="ES256")))
    _rejected(res)


def test_cf_email_is_carried_in_the_claims(
    idp: FakeIdP, clock: FakeClock, rsa_key: KeyPair
) -> None:
    app = _app(CF, idp, clock)

    @app.get("/claims")
    def claims(user: Annotated[Principal, Depends(get_current_user)]) -> dict[str, Any]:
        return {"email": user.claims.get("email")}

    token = sign(rsa_key, _claims(CF, email="a@example.com"), alg="RS256")
    with TestClient(app) as client:
        assert client.get("/claims", headers=CF.headers(token)).json() == {"email": "a@example.com"}


def test_cf_keys_come_from_the_team_certs_endpoint() -> None:
    app = FastAPI()
    settings = Settings(**CF.settings(FakeIdP(keys=[])))
    cache = _jwks_cache(app, settings)
    assert cache._url == f"https://{CF_TEAM}/cdn-cgi/access/certs"
    assert cache._production is False


# --- oidc only ----------------------------------------------------------------


def test_es256(oidc_call: Callable[..., Any], ec_key: KeyPair) -> None:
    res = oidc_call(sign(ec_key, _claims(OIDC), alg="ES256"))
    assert res.status_code == HTTP_OK


def test_es256_header_with_an_rsa_key_kid(
    oidc_call: Callable[..., Any], rsa_key: KeyPair, ec_key: KeyPair
) -> None:
    _rejected(oidc_call(sign(ec_key, _claims(OIDC), alg="ES256", headers={"kid": rsa_key.kid})))


def test_algorithm_outside_the_allowlist(oidc_call: Callable[..., Any], ec_key: KeyPair) -> None:
    _rejected(oidc_call(sign(ec_key, _claims(OIDC), alg="ES256"), OIDC_ALGORITHMS="RS256"))


def test_roles_from_a_list_claim(oidc_call: Callable[..., Any], rsa_key: KeyPair) -> None:
    token = sign(rsa_key, _claims(OIDC, groups=["a", "b"]), alg="RS256")
    assert oidc_call(token, OIDC_ROLES_CLAIM="groups").json()["roles"] == ["a", "b"]


def test_roles_from_a_space_separated_claim(
    oidc_call: Callable[..., Any], rsa_key: KeyPair
) -> None:
    token = sign(rsa_key, _claims(OIDC, scope="read write"), alg="RS256")
    assert oidc_call(token, OIDC_ROLES_CLAIM="scope").json()["roles"] == ["read", "write"]


def test_custom_user_claim(oidc_call: Callable[..., Any], rsa_key: KeyPair) -> None:
    token = sign(rsa_key, _claims(OIDC, uid="u-9"), alg="RS256")
    assert oidc_call(token, OIDC_USER_ID_CLAIM="uid").json()["id"] == "u-9"


def test_multiple_configured_audiences(oidc_call: Callable[..., Any], rsa_key: KeyPair) -> None:
    token = sign(rsa_key, _claims(OIDC, aud="second"), alg="RS256")
    assert oidc_call(token, OIDC_AUDIENCE="first, second").status_code == HTTP_OK


def test_discovery_without_a_jwks_url(idp: FakeIdP, clock: FakeClock, rsa_key: KeyPair) -> None:
    idp.discovery = {"issuer": ISSUER, "jwks_uri": idp.jwks_url}
    app = _app(OIDC, idp, clock)
    app.state.settings = Settings(AUTH_MODE="oidc", OIDC_ISSUER=ISSUER, OIDC_AUDIENCE=[AUDIENCE])
    app.state.jwks_cache = JwksCache(jwks_url=None, issuer=ISSUER, client=idp.client(), clock=clock)
    token = sign(rsa_key, _claims(OIDC), alg="RS256")
    with TestClient(app) as client:
        assert client.get(ME, headers=OIDC.headers(token)).status_code == HTTP_OK
