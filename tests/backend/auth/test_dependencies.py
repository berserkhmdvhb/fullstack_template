# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Tests for the authentication dependency (``backend.auth.dependencies``).

Covers the three ``AUTH_MODE`` behaviours and the role-checking helper:

- ``none`` (default) → anonymous principal, reference CRUD stays open.
- ``gateway`` → identity read from a trusted header; missing → 401; roles parsed.
- ``bearer`` → HMAC-signed JWT verified (alg-confusion rejected, bad/absent
  secret → 401); user id taken from the configured claim.
- ``require_roles`` → 403 when a required role is missing, 200 otherwise.
- An unsupported ``AUTH_MODE`` value fails closed with 401.

These run against a minimal FastAPI app (no database) so they exercise the auth
layer in isolation.
"""

from __future__ import annotations

import base64
import hmac
import json
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, status
from fastapi.testclient import TestClient

from your_service_name.backend.auth import (
    Principal,
    get_current_user,
    require_roles,
)

if TYPE_CHECKING:
    import pytest

ME_PATH: str = "/me"
ADMIN_PATH: str = "/admin"
DEFAULT_SIGNING_KEY: str = "test-signing-key-do-not-use-in-prod"


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
    """Minimal app exposing the principal (``/me``) and a role-gated route."""
    app = FastAPI()

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
        monkeypatch.setenv("AUTH_MODE", "gateway")
        with TestClient(_make_app()) as client:
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
        monkeypatch.setenv("AUTH_MODE", "gateway")
        with TestClient(_make_app()) as client:
            res = client.get(ME_PATH)
        assert res.status_code == status.HTTP_401_UNAUTHORIZED
        assert res.headers["WWW-Authenticate"] == "Bearer"

    def test_custom_header_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AUTH_USER_ID_HEADER retargets the trusted header."""
        monkeypatch.setenv("AUTH_MODE", "gateway")
        monkeypatch.setenv("AUTH_USER_ID_HEADER", "x-trusted-subject")
        with TestClient(_make_app()) as client:
            ok = client.get(ME_PATH, headers={"X-Trusted-Subject": "u9"})
            missing = client.get(ME_PATH)
        assert ok.status_code == status.HTTP_200_OK
        assert ok.json()["user_id"] == "u9"
        assert missing.status_code == status.HTTP_401_UNAUTHORIZED


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

    def test_missing_secret_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Bearer mode without AUTH_JWT_SECRET must NOT silently allow access."""
        monkeypatch.setenv("AUTH_MODE", "bearer")
        monkeypatch.delenv("AUTH_JWT_SECRET", raising=False)
        with TestClient(_make_app()) as client:
            res = client.get(ME_PATH)
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

    def test_unsupported_algorithm_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A non-HMAC algorithm is refused outright (no key-confusion surface)."""
        monkeypatch.setenv("AUTH_MODE", "bearer")
        monkeypatch.setenv("AUTH_JWT_ALGORITHM", "none")
        monkeypatch.setenv("AUTH_JWT_SECRET", DEFAULT_SIGNING_KEY)
        with TestClient(_make_app()) as client:
            res = client.get(ME_PATH, headers={"Authorization": "Bearer whatever"})
        assert res.status_code == status.HTTP_401_UNAUTHORIZED


# --- unsupported mode ---------------------------------------------------------


class TestUnsupportedMode:
    """An unknown AUTH_MODE value fails closed."""

    def test_unknown_mode_is_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A typo/garbage AUTH_MODE rejects protected requests with 401."""
        monkeypatch.setenv("AUTH_MODE", "ldap")
        with TestClient(_make_app()) as client:
            res = client.get(ME_PATH)
        assert res.status_code == status.HTTP_401_UNAUTHORIZED


# --- role authorization -------------------------------------------------------


class TestRequireRoles:
    """``require_roles`` gates a route on the principal's roles."""

    def test_required_role_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A principal carrying the required role is allowed."""
        monkeypatch.setenv("AUTH_MODE", "gateway")
        with TestClient(_make_app()) as client:
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
        monkeypatch.setenv("AUTH_MODE", "gateway")
        with TestClient(_make_app()) as client:
            res = client.get(
                ADMIN_PATH,
                headers={
                    "X-Authenticated-User-Id": "u1",
                    "X-Authenticated-User-Roles": "editor",
                },
            )
        assert res.status_code == status.HTTP_403_FORBIDDEN
