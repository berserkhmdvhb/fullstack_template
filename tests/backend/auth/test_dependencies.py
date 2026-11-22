# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Tests for the authentication dependency (``backend.auth.dependencies``).

Covers the three ``AUTH_MODE`` behaviours and the role-checking helper:

- ``none`` (default) → anonymous principal, reference CRUD stays open.
- ``gateway`` → identity read from a trusted header; missing → 401; roles parsed. The header
  counts only when the direct TCP peer is inside ``AUTH_PROXY_CIDRS``; any other peer → 401.
- ``bearer`` → HMAC-signed JWT verified (alg-confusion rejected, bad/absent
  secret → 401); user id taken from the configured claim.
- ``require_roles`` → 403 when a required role is missing, 200 otherwise.
- An unsupported ``AUTH_MODE`` value, a bearer mode without a secret, or a non-HMAC algorithm
  is refused at startup (no app is built).

These run against a minimal FastAPI app (no database) so they exercise the auth
layer in isolation.
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI, status
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from your_service_name.backend.auth import (
    Principal,
    get_current_user,
    require_roles,
)
from your_service_name.backend.middleware.client_ip import ClientIpMiddleware
from your_service_name.backend.settings import Settings

ME_PATH: str = "/me"
ADMIN_PATH: str = "/admin"
DEFAULT_SIGNING_KEY: str = "test-signing-key-do-not-use-in-prod"

# Peers (TEST-NET and private ranges only).
AUTH_PROXY_RANGE: str = "10.20.0.0/16"  # AUTH_PROXY_CIDRS: the proxy that authenticates
AUTH_PROXY: str = "10.20.0.7"  # a Pod of that proxy
EDGE_RANGE: str = "10.9.0.0/16"  # TRUSTED_PROXY_CIDRS only: a client-IP hop, not an auth proxy
EDGE: str = "10.9.0.5"
STRANGER: str = "198.51.100.9"  # any other peer that can reach the backend
SPOOFED: dict[str, str] = {
    "X-Authenticated-User-Id": "spoofed-admin",
    "X-Authenticated-User-Roles": "admin",
}


# --- helpers ------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    """Base64url-encode without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_jwt(
    payload: dict[str, Any],
    *,
    signing_key: str = DEFAULT_SIGNING_KEY,
    alg: str = "HS256",
    header: dict[str, Any] | None = None,
) -> str:
    """
    Build an HMAC-signed JWT the same way ``_verify_hs`` expects one.

    Parameters
    ----------
    payload : dict[str, Any]
        Claim set (e.g. ``{"sub": "u1"}``).
    signing_key : str
        Shared HMAC secret.
    alg : str
        Algorithm name (``HS256``/``HS384``/``HS512``); written into the header.
    header : dict[str, Any] or None
        Full header override (used to forge an alg-confusion token in tests).

    Returns
    -------
    str
        The serialized ``header.payload.signature`` token.
    """
    hdr = header if header is not None else {"alg": alg, "typ": "JWT"}
    header_seg = _b64url(json.dumps(hdr, separators=(",", ":")).encode())
    payload_seg = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_seg}.{payload_seg}".encode()
    # JWT "HS256" is hashlib "sha256" (there is no "hs256").
    digest = {"HS256": "sha256", "HS384": "sha384", "HS512": "sha512"}[alg]
    signature = hmac.new(signing_key.encode(), signing_input, digest).digest()
    return f"{header_seg}.{payload_seg}.{_b64url(signature)}"


def _make_app() -> FastAPI:
    """Minimal app exposing the principal (``/me``) and a role-gated route.

    The settings are built from the environment at construction (as ``create_app`` does), so a
    test that changes the environment builds a new app.
    """
    app = FastAPI()
    app.state.settings = Settings()
    # As in create_app: the client-IP resolver rewrites the client from a trusted hop's headers.
    app.add_middleware(ClientIpMiddleware, trusted=app.state.settings.TRUSTED_PROXY_CIDRS)

    @app.get(ME_PATH)
    def me(current_user: Annotated[Principal, Depends(get_current_user)]) -> dict[str, Any]:
        return {
            "user_id": current_user.user_id,
            "source": current_user.identity_source,
            "roles": current_user.roles,
            "anon": current_user.is_anonymous,
        }

    @app.get(ADMIN_PATH)
    def admin(
        _current_user: Annotated[Principal, Depends(require_roles("admin"))],
    ) -> dict[str, bool]:
        return {"ok": True}

    return app


def _client(app: FastAPI, peer: str = AUTH_PROXY) -> TestClient:
    """A client whose requests arrive from the TCP peer ``peer``."""
    return TestClient(app, client=(peer, 50000))


def _gateway(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    """Gateway mode with the auth proxy's range listed (then overrides)."""
    for name, value in {
        "AUTH_MODE": "gateway",
        "AUTH_PROXY_CIDRS": AUTH_PROXY_RANGE,
        **env,
    }.items():
        monkeypatch.setenv(name, value)


# --- none mode (auth disabled) ------------------------------------------------


class TestNoneMode:
    """``AUTH_MODE=none`` (the default) yields an anonymous principal."""

    def test_anonymous_principal_by_default(self) -> None:
        """With no AUTH_MODE set, /me resolves to an anonymous principal."""
        with TestClient(_make_app()) as client:
            res = client.get(ME_PATH)
        assert res.status_code == status.HTTP_200_OK
        assert res.json() == {"user_id": "anonymous", "source": "none", "roles": [], "anon": True}


# --- gateway mode -------------------------------------------------------------


class TestGatewayMode:
    """``AUTH_MODE=gateway`` reads identity from a trusted header."""

    def test_header_yields_principal_with_roles(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A request carrying the identity + roles headers is accepted."""
        _gateway(monkeypatch)
        with _client(_make_app()) as client:
            res = client.get(
                ME_PATH,
                headers={
                    "X-Authenticated-User-Id": "user-123",
                    "X-Authenticated-User-Roles": "admin, editor",
                },
            )
        assert res.status_code == status.HTTP_200_OK
        assert res.json() == {
            "user_id": "user-123",
            "source": "gateway",
            "roles": ["admin", "editor"],
            "anon": False,
        }

    def test_missing_header_is_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A request without the identity header is rejected with 401."""
        _gateway(monkeypatch)
        with _client(_make_app()) as client:
            res = client.get(ME_PATH)
        assert res.status_code == status.HTTP_401_UNAUTHORIZED
        assert res.headers["WWW-Authenticate"] == "Bearer"

    def test_custom_header_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AUTH_USER_ID_HEADER retargets the trusted header."""
        _gateway(monkeypatch)
        monkeypatch.setenv("AUTH_USER_ID_HEADER", "x-trusted-subject")
        with _client(_make_app()) as client:
            ok = client.get(ME_PATH, headers={"X-Trusted-Subject": "u9"})
            missing = client.get(ME_PATH)
        assert ok.status_code == status.HTTP_200_OK
        assert ok.json()["user_id"] == "u9"
        assert missing.status_code == status.HTTP_401_UNAUTHORIZED


class TestGatewayTrustedPeer:
    """The identity headers count only from a direct TCP peer inside AUTH_PROXY_CIDRS."""

    def test_spoofed_header_from_an_untrusted_peer_is_401(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Peer STRANGER (outside AUTH_PROXY_CIDRS) sends the headers: 401, values not logged."""
        _gateway(monkeypatch)
        with caplog.at_level(logging.INFO), _client(_make_app(), STRANGER) as client:
            res = client.get(ME_PATH, headers=SPOOFED)
        assert res.status_code == status.HTTP_401_UNAUTHORIZED
        assert res.headers["WWW-Authenticate"] == "Bearer"
        assert "AUTH_PROXY_CIDRS" in caplog.text
        assert "spoofed-admin" not in caplog.text

    def test_header_from_a_peer_in_the_list_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Peer AUTH_PROXY (inside AUTH_PROXY_CIDRS) is believed."""
        _gateway(monkeypatch)
        with _client(_make_app(), AUTH_PROXY) as client:
            res = client.get(ME_PATH, headers=SPOOFED)
        assert res.status_code == status.HTTP_200_OK
        assert res.json()["user_id"] == "spoofed-admin"

    def test_a_forwarded_auth_proxy_address_does_not_count(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Peer EDGE is a trusted client-IP hop (not an auth proxy) and claims, in X-Forwarded-For,
        that the request came from AUTH_PROXY: the client IP becomes AUTH_PROXY, the identity
        headers still get a 401, because only the direct peer counts."""
        _gateway(monkeypatch, TRUSTED_PROXY_CIDRS=EDGE_RANGE)
        with _client(_make_app(), EDGE) as client:
            res = client.get(ME_PATH, headers={**SPOOFED, "X-Forwarded-For": AUTH_PROXY})
        assert res.status_code == status.HTTP_401_UNAUTHORIZED

    def test_an_auth_proxy_that_is_also_a_client_ip_hop_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Peer AUTH_PROXY is in both lists and forwards a client address: still believed."""
        _gateway(monkeypatch, TRUSTED_PROXY_CIDRS=AUTH_PROXY_RANGE)
        with _client(_make_app(), AUTH_PROXY) as client:
            res = client.get(ME_PATH, headers={**SPOOFED, "X-Forwarded-For": STRANGER})
        assert res.status_code == status.HTTP_200_OK

    def test_the_untrusted_peer_rejection_logs_at_info(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Peer STRANGER: a per-request rejection is INFO (as elsewhere), never WARNING."""
        _gateway(monkeypatch)
        with caplog.at_level(logging.INFO), _client(_make_app(), STRANGER) as client:
            client.get(ME_PATH, headers=SPOOFED)
        records = [r for r in caplog.records if "AUTH_PROXY_CIDRS" in r.getMessage()]
        assert [r.levelno for r in records] == [logging.INFO]

    @pytest.mark.parametrize(
        "headers",
        [
            # The client's forged copy first, the proxy's appended copy second, and the reverse.
            [("X-Authenticated-User-Id", "forged"), ("x-authenticated-user-id", "real")],
            [("x-authenticated-user-id", "real"), ("X-AUTHENTICATED-USER-ID", "forged")],
            # One id, two roles headers.
            [
                ("X-Authenticated-User-Id", "real"),
                ("X-Authenticated-User-Roles", "admin"),
                ("x-authenticated-user-roles", "viewer"),
            ],
        ],
        ids=["id-forged-first", "id-forged-second", "roles-twice"],
    )
    def test_a_repeated_identity_or_roles_header_is_401(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        headers: list[tuple[str, str]],
    ) -> None:
        """Peer AUTH_PROXY (trusted) sends a header twice: fail closed, log no values."""
        _gateway(monkeypatch)
        with caplog.at_level(logging.INFO), _client(_make_app(), AUTH_PROXY) as client:
            res = client.get(ME_PATH, headers=headers)
        assert res.status_code == status.HTTP_401_UNAUTHORIZED
        records = [r for r in caplog.records if "more than once" in r.getMessage()]
        assert [r.levelno for r in records] == [logging.INFO]
        for value in ("forged", "real", "admin", "viewer"):
            assert value not in caplog.text

    def test_a_repeated_configured_header_is_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Peer AUTH_PROXY: the count follows AUTH_USER_ID_HEADER, in any letter case."""
        _gateway(monkeypatch, AUTH_USER_ID_HEADER="X-Trusted-Subject")
        headers = [("x-trusted-subject", "a"), ("X-Trusted-Subject", "b")]
        with _client(_make_app(), AUTH_PROXY) as client:
            res = client.get(ME_PATH, headers=headers)
        assert res.status_code == status.HTTP_401_UNAUTHORIZED

    def test_a_single_header_from_a_trusted_peer_is_still_200(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Peer AUTH_PROXY: one id and one roles header pass."""
        _gateway(monkeypatch)
        with _client(_make_app(), AUTH_PROXY) as client:
            res = client.get(ME_PATH, headers=[*SPOOFED.items()])
        assert res.status_code == status.HTTP_200_OK
        assert res.json()["roles"] == ["admin"]

    @pytest.mark.parametrize("value", ["   ", "\t", " \t "])
    def test_a_blank_identity_from_a_trusted_peer_is_401(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        """Peer AUTH_PROXY sends an id that is empty after strip(): 401."""
        _gateway(monkeypatch)
        with _client(_make_app(), AUTH_PROXY) as client:
            res = client.get(ME_PATH, headers={"X-Authenticated-User-Id": value})
        assert res.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize("peer", [AUTH_PROXY, STRANGER, "testclient"])
    def test_an_empty_list_trusts_nobody(self, monkeypatch: pytest.MonkeyPatch, peer: str) -> None:
        """AUTH_PROXY_CIDRS empty: every peer, whatever its address, gets a 401."""
        _gateway(monkeypatch, AUTH_PROXY_CIDRS="")
        with _client(_make_app(), peer) as client:
            res = client.get(ME_PATH, headers=SPOOFED)
        assert res.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize(
        ("peer", "expected"),
        [
            ("fd20::7", status.HTTP_200_OK),  # inside fd20::/64
            ("fd21::7", status.HTTP_401_UNAUTHORIZED),  # outside it
            ("::ffff:10.20.0.7", status.HTTP_200_OK),  # IPv4-mapped AUTH_PROXY
        ],
    )
    def test_ipv6_peers(self, monkeypatch: pytest.MonkeyPatch, peer: str, expected: int) -> None:
        """IPv6 ranges work, and an IPv4-mapped peer is matched as IPv4."""
        _gateway(monkeypatch, AUTH_PROXY_CIDRS=f"fd20::/64,{AUTH_PROXY_RANGE}")
        with _client(_make_app(), peer) as client:
            res = client.get(ME_PATH, headers=SPOOFED)
        assert res.status_code == expected


# --- bearer mode (minimal HS* verifier) ---------------------------------------


class TestBearerMode:
    """``AUTH_MODE=bearer`` verifies an HMAC-signed JWT."""

    def test_valid_token_yields_principal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A correctly signed token resolves the principal from its claim."""
        monkeypatch.setenv("AUTH_MODE", "bearer")
        monkeypatch.setenv("AUTH_JWT_SECRET", DEFAULT_SIGNING_KEY)
        token = make_jwt({"sub": "user-42", "role": "admin"})
        with TestClient(_make_app()) as client:
            res = client.get(ME_PATH, headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == status.HTTP_200_OK
        assert res.json()["user_id"] == "user-42"
        assert res.json()["source"] == "bearer"

    def test_bad_signature_is_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A token signed with the wrong secret is rejected."""
        monkeypatch.setenv("AUTH_MODE", "bearer")
        monkeypatch.setenv("AUTH_JWT_SECRET", DEFAULT_SIGNING_KEY)
        token = make_jwt({"sub": "user-42"}, signing_key="wrong-secret")
        with TestClient(_make_app()) as client:
            res = client.get(ME_PATH, headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == status.HTTP_401_UNAUTHORIZED

    def test_missing_header_is_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No Authorization header → 401."""
        monkeypatch.setenv("AUTH_MODE", "bearer")
        monkeypatch.setenv("AUTH_JWT_SECRET", DEFAULT_SIGNING_KEY)
        with TestClient(_make_app()) as client:
            res = client.get(ME_PATH)
        assert res.status_code == status.HTTP_401_UNAUTHORIZED

    def test_missing_secret_is_refused_at_startup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Bearer mode without AUTH_JWT_SECRET must NOT silently allow access: no app at all."""
        monkeypatch.setenv("AUTH_MODE", "bearer")
        monkeypatch.delenv("AUTH_JWT_SECRET", raising=False)
        with pytest.raises(ValidationError, match="AUTH_JWT_SECRET"):
            _make_app()

    @pytest.mark.parametrize(
        ("unvalidated", "token_kwargs"),
        [
            ({"AUTH_JWT_SECRET": None}, {}),
            # An empty HMAC key would accept a token signed with the empty key.
            ({"AUTH_JWT_SECRET": SecretStr("")}, {"signing_key": ""}),
            (
                {"AUTH_JWT_SECRET": SecretStr(DEFAULT_SIGNING_KEY), "AUTH_JWT_ALGORITHM": "RS256"},
                {"header": {"alg": "RS256", "typ": "JWT"}},
            ),
            (
                {"AUTH_JWT_SECRET": SecretStr(DEFAULT_SIGNING_KEY), "AUTH_JWT_ALGORITHM": "none"},
                {"header": {"alg": "none", "typ": "JWT"}},
            ),
        ],
    )
    def test_unvalidated_bearer_settings_fail_closed_with_401(
        self, unvalidated: dict[str, Any], token_kwargs: dict[str, Any]
    ) -> None:
        """Defence in depth: settings built without validation give 401, never 200 or a 500."""
        app = _make_app()
        app.state.settings = Settings.model_construct(AUTH_MODE="bearer", **unvalidated)
        token = make_jwt({"sub": "u1"}, **token_kwargs)
        with TestClient(app, raise_server_exceptions=False) as client:
            res = client.get(ME_PATH, headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == status.HTTP_401_UNAUTHORIZED

    def test_alg_confusion_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A token whose header alg differs from the configured one is rejected."""
        monkeypatch.setenv("AUTH_MODE", "bearer")
        monkeypatch.setenv("AUTH_JWT_ALGORITHM", "HS256")
        monkeypatch.setenv("AUTH_JWT_SECRET", DEFAULT_SIGNING_KEY)
        # Signed HS256 but the header *claims* HS384 — must not be accepted.
        token = make_jwt({"sub": "user-42"}, alg="HS384")
        with TestClient(_make_app()) as client:
            res = client.get(ME_PATH, headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == status.HTTP_401_UNAUTHORIZED

    def test_unsupported_algorithm_is_refused_at_startup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-HMAC algorithm is refused outright (no key-confusion surface)."""
        monkeypatch.setenv("AUTH_MODE", "bearer")
        monkeypatch.setenv("AUTH_JWT_ALGORITHM", "none")
        monkeypatch.setenv("AUTH_JWT_SECRET", DEFAULT_SIGNING_KEY)
        with pytest.raises(ValidationError, match="AUTH_JWT_ALGORITHM"):
            _make_app()


# --- unsupported mode ---------------------------------------------------------


class TestUnsupportedMode:
    """An unknown AUTH_MODE value is refused at startup."""

    def test_unknown_mode_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A typo/garbage AUTH_MODE fails app construction instead of 401-ing every request."""
        monkeypatch.setenv("AUTH_MODE", "ldap")
        with pytest.raises(ValidationError, match="AUTH_MODE"):
            _make_app()


# --- role authorization -------------------------------------------------------


class TestRequireRoles:
    """``require_roles`` gates a route on the principal's roles."""

    def test_required_role_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A principal carrying the required role is allowed."""
        _gateway(monkeypatch)
        with _client(_make_app()) as client:
            res = client.get(
                ADMIN_PATH,
                headers={
                    "X-Authenticated-User-Id": "u1",
                    "X-Authenticated-User-Roles": "admin",
                },
            )
        assert res.status_code == status.HTTP_200_OK
        assert res.json() == {"ok": True}

    def test_required_role_missing_is_403(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A principal missing the required role is rejected with 403."""
        _gateway(monkeypatch)
        with _client(_make_app()) as client:
            res = client.get(
                ADMIN_PATH,
                headers={
                    "X-Authenticated-User-Id": "u1",
                    "X-Authenticated-User-Roles": "editor",
                },
            )
        assert res.status_code == status.HTTP_403_FORBIDDEN
