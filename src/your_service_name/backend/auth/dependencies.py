# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Authentication dependency for the FastAPI backend.

Responsibilities:
- Resolve the authenticated principal for a request via a FastAPI dependency,
  driven entirely by configuration so the template imposes no IdP of its own.

This is a *reference pattern*, not a production IdP. The template's headline
architecture is an API gateway that terminates TLS and authn and forwards a
*trusted* identity header to the service (the service trusts the gateway because
network policy denies any client but the ingress from reaching it). A JWT bearer
mode is provided for adopters who terminate authn in the service itself.

Modes (``AUTH_MODE`` env var):
- ``none``    (default) — auth disabled; the reference CRUD stays open and the
              dependency yields an anonymous principal. Opt in by setting the env.
- ``gateway`` — identity is read from a gateway-injected header
              (``AUTH_USER_ID_HEADER``, default ``X-Authenticated-User-Id``). A
              request without it is rejected with 401.
- ``bearer``  — identity comes from a signed JWT bearer token, verified with a
              shared HMAC secret (``AUTH_JWT_SECRET``). Missing/invalid → 401.

Trust boundary (see wiki/security.md): in ``gateway`` mode the service trusts the
header unconditionally — that is only safe because the deployment denies every
client but the ingress the path to the pods (NetworkPolicy default-deny). Do not
expose the service directly to untrusted clients in this mode.

Public API:
- `get_current_user`: FastAPI dependency resolving the request principal.
- `require_roles`: helper to build a role-checking dependency (authZ).
- `NotAuthenticatedError`: 401 raised when identity is missing/invalid.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import logging
import os
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, HTTPException, Request, status

from your_service_name.backend.auth.principal import Principal

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# --- Configuration (all env-driven; sane template defaults) -------------------
_AUTH_MODE_ENV: str = "AUTH_MODE"
_GATEWAY_HEADER_ENV: str = "AUTH_USER_ID_HEADER"
_ROLES_HEADER_ENV: str = "AUTH_ROLES_HEADER"
# Env-var *name* constant (not a secret — the value is the string "AUTH_JWT_SECRET").
_JWT_SECRET_ENV: str = "AUTH_JWT_SECRET"  # noqa: S105
_JWT_ALGORITHM_ENV: str = "AUTH_JWT_ALGORITHM"
_JWT_USER_ID_CLAIM_ENV: str = "AUTH_JWT_USER_ID_CLAIM"

DEFAULT_GATEWAY_HEADER: str = "x-authenticated-user-id"
DEFAULT_ROLES_HEADER: str = "x-authenticated-user-roles"
DEFAULT_JWT_ALGORITHM: str = "HS256"
DEFAULT_JWT_USER_ID_CLAIM: str = "sub"

# A compact JWT has exactly three dot-separated segments: header.payload.signature.
_JWT_SEGMENT_COUNT: int = 3

# HMAC family only. RSA/ECDSA/`none` are deliberately rejected to avoid the
# classic "alg confusion" attack (an attacker swapping alg to bypass the key).
_ALLOWED_JWT_ALGS: frozenset[str] = frozenset({"HS256", "HS384", "HS512"})

# JWT alg name → hashlib digest name (a JWT "HS256" is hashlib "sha256", not
# the non-existent "hs256"). Single source of truth for signing/verifying.
_JWT_HASHLIB: dict[str, str] = {"HS256": "sha256", "HS384": "sha384", "HS512": "sha512"}

_AUTHORIZATION_PREFIX: str = "bearer "


class NotAuthenticatedError(HTTPException):
    """401 raised when a protected endpoint is called without valid identity."""

    def __init__(self, detail: str, *, scheme: str = "Bearer") -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": scheme},
        )


class InvalidTokenError(Exception):
    """Raised by the minimal JWT verifier when a token cannot be validated."""


def get_current_user(request: Request) -> Principal:
    """
    Resolve the principal for the current request.

    The behaviour is selected by ``AUTH_MODE`` so the reference service ships with
    auth *off* (open CRUD, existing flows intact) and an adopter opts in by setting
    the env var — no code change required.

    Parameters
    ----------
    request : fastapi.Request
        Injected by FastAPI.

    Returns
    -------
    Principal
        The resolved identity (anonymous when ``AUTH_MODE=none``).

    Raises
    ------
    NotAuthenticatedError
        If the configured mode requires identity and none is present/valid.
    """
    mode = (os.getenv(_AUTH_MODE_ENV) or "none").strip().lower()
    if mode == "gateway":
        return _principal_from_gateway(request)
    if mode == "bearer":
        return _principal_from_bearer(request)
    if mode in ("", "none"):
        return Principal(user_id="anonymous", identity_source="none")
    msg = f"Unsupported AUTH_MODE: {mode!r}"
    raise NotAuthenticatedError(msg)


def require_roles(*required: str) -> Callable[[Principal], Principal]:
    """
    Build a dependency that requires the principal to hold all given roles.

    Parameters
    ----------
    *required : str
        Role names the principal must carry.

    Returns
    -------
    Callable[[Principal], Principal]
        A dependency returning the principal, or raising 403.
    """

    def _dependency(
        principal: Annotated[Principal, Depends(get_current_user)],
    ) -> Principal:
        missing = [role for role in required if not principal.has_role(role)]
        if missing:
            detail = f"Missing required role(s): {', '.join(missing)}"
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
        return principal

    return _dependency


# --- gateway mode -------------------------------------------------------------


def _principal_from_gateway(request: Request) -> Principal:
    """Resolve identity from a gateway-injected trusted header."""
    header = os.getenv(_GATEWAY_HEADER_ENV) or DEFAULT_GATEWAY_HEADER
    user_id = request.headers.get(header)
    if not user_id:
        msg = (
            f"Gateway identity header {header!r} not present (AUTH_MODE=gateway). "
            "Only the trusted ingress may set it."
        )
        raise NotAuthenticatedError(msg)
    roles: list[str] = []
    roles_header = os.getenv(_ROLES_HEADER_ENV) or DEFAULT_ROLES_HEADER
    raw_roles = request.headers.get(roles_header)
    if raw_roles:
        roles = [r.strip() for r in raw_roles.split(",") if r.strip()]
    return Principal(user_id=user_id, identity_source="gateway", roles=roles)


# --- bearer mode (minimal, dependency-free HS* verifier) ----------------------


def _b64url_decode_json(segment: str) -> dict[str, Any]:
    """Base64url-decode a JWT segment and parse it as a JSON object."""
    padding = "=" * (-len(segment) % 4)
    try:
        raw = base64.urlsafe_b64decode(segment + padding)
    except (binascii.Error, ValueError) as exc:
        error_message = "malformed token segment"
        raise InvalidTokenError(error_message) from exc
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        error_message = "invalid token segment"
        raise InvalidTokenError(error_message) from exc
    if not isinstance(data, dict):
        error_message = "token segment is not a JSON object"
        raise InvalidTokenError(error_message)
    return data


def _verify_hs(token: str, secret: bytes, alg: str) -> dict[str, Any]:
    """
    Verify an HMAC-signed JWT and return its payload.

    Only the HMAC family is accepted (see ``_ALLOWED_JWT_ALGS``); the header ``alg``
    must match the configured one, which defeats alg-confusion attacks.
    """
    parts = token.split(".")
    if len(parts) != _JWT_SEGMENT_COUNT:
        error_message = "token must have three segments"
        raise InvalidTokenError(error_message)

    header = _b64url_decode_json(parts[0])
    if header.get("alg") != alg:
        error_message = "unexpected token alg"
        raise InvalidTokenError(error_message)

    signing_input = f"{parts[0]}.{parts[1]}".encode()
    digest = _JWT_HASHLIB[alg]
    expected = hmac.new(secret, signing_input, digest).digest()
    try:
        provided = base64.urlsafe_b64decode(parts[2] + "=" * (-len(parts[2]) % 4))
    except (binascii.Error, ValueError) as exc:
        error_message = "malformed signature"
        raise InvalidTokenError(error_message) from exc
    if not hmac.compare_digest(expected, provided):
        error_message = "bad signature"
        raise InvalidTokenError(error_message)

    return _b64url_decode_json(parts[1])


def _principal_from_bearer(request: Request) -> Principal:
    """Resolve identity from a signed JWT bearer token."""
    authorization = request.headers.get("Authorization", "")
    if not authorization.lower().startswith(_AUTHORIZATION_PREFIX):
        msg = "Missing Bearer token (AUTH_MODE=bearer)."
        raise NotAuthenticatedError(msg)
    token = authorization[len(_AUTHORIZATION_PREFIX) :].strip()
    if not token:
        msg = "Empty Bearer token (AUTH_MODE=bearer)."
        raise NotAuthenticatedError(msg)

    secret = os.getenv(_JWT_SECRET_ENV)
    if not secret:
        # Fail closed: misconfigured bearer mode must not silently allow access.
        msg = "AUTH_JWT_SECRET is not set (AUTH_MODE=bearer)."
        raise NotAuthenticatedError(msg)

    alg = (os.getenv(_JWT_ALGORITHM_ENV) or DEFAULT_JWT_ALGORITHM).upper()
    if alg not in _ALLOWED_JWT_ALGS:
        msg = f"Unsupported JWT algorithm: {alg!r}."
        raise NotAuthenticatedError(msg)

    try:
        payload = _verify_hs(token, secret.encode(), alg)
    except InvalidTokenError as exc:
        msg = f"Invalid auth token: {exc}"
        raise NotAuthenticatedError(msg) from exc

    claim = os.getenv(_JWT_USER_ID_CLAIM_ENV) or DEFAULT_JWT_USER_ID_CLAIM
    user_id = payload.get(claim)
    if not user_id:
        msg = f"Auth token missing claim {claim!r}."
        raise NotAuthenticatedError(msg)
    return Principal(user_id=str(user_id), identity_source="bearer", claims=payload)
