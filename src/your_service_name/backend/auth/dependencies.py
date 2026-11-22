# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Authentication dependency for the FastAPI backend.

Responsibilities:
- Resolve the authenticated principal for a request via a FastAPI dependency,
  driven entirely by configuration so the template imposes no IdP of its own.

This is a *reference pattern*, not a production IdP. In ``gateway`` mode an auth
proxy authenticates the user and forwards a *trusted* identity header to the
service; the service believes it only because the request's direct TCP peer is
inside ``AUTH_PROXY_CIDRS``, never because of who network policy lets reach it. A
JWT bearer mode is provided for adopters who terminate authn in the service itself.

Modes (``AUTH_MODE`` setting; see ``backend/settings.py``, validated at startup):
- ``none``    (default) — auth disabled; the reference CRUD stays open and the
              dependency yields an anonymous principal. Opt in by setting the env.
- ``gateway`` — identity is read from a header injected by the auth proxy
              (``AUTH_USER_ID_HEADER``, default ``X-Authenticated-User-Id``), and
              only when the direct TCP peer is inside ``AUTH_PROXY_CIDRS``. Any other
              peer, or a request without the header, is rejected with 401.
- ``bearer``  — identity comes from a signed JWT bearer token, verified with a
              shared HMAC secret (``AUTH_JWT_SECRET``). Missing/invalid → 401.
- ``oidc``    — production mode: ``Authorization: Bearer <jwt>`` from an OpenID Connect
              provider, verified (RS*/PS*/ES*) against its cached JWKS. Missing/invalid → 401;
              keys unobtainable → 503.
- ``cloudflare-access`` — production mode behind Cloudflare Access: the application token in the
              ``Cf-Access-Jwt-Assertion`` header (never the ``CF_Authorization`` cookie), verified
              (RS256) against the team's certs endpoint and the application's AUD tag.

``gateway`` and ``bearer`` are *reference* modes (a trusted header; a shared-secret scheme for
internal or test use, not for end-user identity); ``oidc`` is for production.

Trust boundary (see wiki/security.md): in ``gateway`` mode the identity and roles
headers count only from a direct TCP peer inside ``AUTH_PROXY_CIDRS`` (empty trusts
nobody). Who sent the header decides, not network reachability: a forwarded address
(``X-Forwarded-For``) never counts, and the frontend's proxy strips these headers.

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
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, HTTPException, Request, status

from your_service_name.backend.auth.jwks import JwksCache, JwksUnavailableError
from your_service_name.backend.auth.jwt_verify import TokenRejectedError, verify_token
from your_service_name.backend.auth.principal import Principal
from your_service_name.backend.middleware.client_ip import direct_peer, peer_in
from your_service_name.backend.settings import JWT_HASHLIB

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI

    from your_service_name.backend.settings import Settings

logger = logging.getLogger(__name__)

# A compact JWT has exactly three dot-separated segments: header.payload.signature.
_JWT_SEGMENT_COUNT: int = 3

_AUTHORIZATION_PREFIX: str = "bearer "


class NotAuthenticatedError(HTTPException):
    """401 raised when a protected endpoint is called without valid identity."""

    def __init__(self, detail: str, *, scheme: str = "Bearer") -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": scheme},
        )


class AuthUnavailableError(HTTPException):
    """503 raised when the signing keys cannot be obtained (never "allow", never a 500)."""

    #: Read by the problem handler: this detail is ours and safe to show.
    expose_detail: bool = True

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authentication temporarily unavailable",
        )


class InvalidTokenError(Exception):
    """Raised by the minimal JWT verifier when a token cannot be validated."""


async def get_current_user(request: Request) -> Principal:
    """
    Resolve the principal for the current request.

    The behaviour is selected by the ``AUTH_MODE`` setting (read from
    ``app.state.settings``) so the reference service ships with auth *off* (open CRUD,
    existing flows intact) and an adopter opts in by setting the env var — no code
    change required. Each mode's required settings are validated at startup.

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
    settings: Settings = request.app.state.settings
    if settings.AUTH_MODE == "gateway":
        return _principal_from_gateway(request, settings)
    if settings.AUTH_MODE == "bearer":
        return _principal_from_bearer(request, settings)
    if settings.AUTH_MODE == "oidc":
        return await _principal_from_oidc(request, settings)
    if settings.AUTH_MODE == "cloudflare-access":
        return await _principal_from_cloudflare_access(request, settings)
    return Principal(user_id="anonymous", identity_source="none")


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


def _header_count(request: Request, name: str) -> int:
    """How many times header ``name`` appears in the raw request (names match in any case)."""
    wanted = name.lower().encode("latin-1")
    return sum(1 for key, _ in request.scope.get("headers", []) if key.lower() == wanted)


def _principal_from_gateway(request: Request, settings: Settings) -> Principal:
    """Resolve identity from the auth proxy's headers; only a direct peer in the list counts.

    Never log a header value here: it is the caller's claim, not an identity.
    """
    if not peer_in(direct_peer(request.scope), settings.AUTH_PROXY_CIDRS):
        logger.info("Gateway identity refused: the TCP peer is not in AUTH_PROXY_CIDRS")
        msg = "Gateway identity is accepted only from the auth proxy (AUTH_MODE=gateway)."
        raise NotAuthenticatedError(msg)
    header = settings.AUTH_USER_ID_HEADER
    roles_header = settings.AUTH_ROLES_HEADER
    # A second copy means a client's copy got past the proxy (one that appends, not replaces):
    # which one is real cannot be told, so neither is believed.
    if _header_count(request, header) > 1 or _header_count(request, roles_header) > 1:
        logger.info("Gateway identity refused: an identity or roles header appears more than once")
        msg = "Gateway identity headers must appear once (AUTH_MODE=gateway)."
        raise NotAuthenticatedError(msg)
    user_id = (request.headers.get(header) or "").strip()
    if not user_id:
        msg = (
            f"Gateway identity header {header!r} not present (AUTH_MODE=gateway). "
            "Only the auth proxy may set it."
        )
        raise NotAuthenticatedError(msg)
    # An id never holds a comma: one means a proxy joined its value onto a client's copy. (Roles
    # are a comma-separated list by design, so the same check cannot apply to them.)
    if "," in user_id:
        logger.info("Gateway identity refused: the identity header holds a comma")
        msg = "Gateway identity header must hold one value (AUTH_MODE=gateway)."
        raise NotAuthenticatedError(msg)
    roles: list[str] = []
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

    Only the HMAC family is accepted (see ``ALLOWED_JWT_ALGS`` in settings); the header ``alg``
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
    digest = JWT_HASHLIB[alg]
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


def _principal_from_bearer(request: Request, settings: Settings) -> Principal:
    """Resolve identity from a signed JWT bearer token."""
    authorization = request.headers.get("Authorization", "")
    if not authorization.lower().startswith(_AUTHORIZATION_PREFIX):
        msg = "Missing Bearer token (AUTH_MODE=bearer)."
        raise NotAuthenticatedError(msg)
    token = authorization[len(_AUTHORIZATION_PREFIX) :].strip()
    if not token:
        msg = "Empty Bearer token (AUTH_MODE=bearer)."
        raise NotAuthenticatedError(msg)

    # Settings validation already refuses bearer mode without a secret or with a non-HMAC
    # algorithm; failing closed here covers a Settings built without validation.
    secret = settings.AUTH_JWT_SECRET
    if secret is None or not secret.get_secret_value():
        msg = "AUTH_JWT_SECRET is not set (AUTH_MODE=bearer)."
        raise NotAuthenticatedError(msg)
    alg = settings.AUTH_JWT_ALGORITHM
    if alg not in JWT_HASHLIB:
        msg = f"Unsupported JWT algorithm: {alg!r}."
        raise NotAuthenticatedError(msg)

    try:
        payload = _verify_hs(token, secret.get_secret_value().encode(), alg)
    except InvalidTokenError as exc:
        msg = f"Invalid auth token: {exc}"
        raise NotAuthenticatedError(msg) from exc

    claim = settings.AUTH_JWT_USER_ID_CLAIM
    user_id = payload.get(claim)
    if not user_id:
        msg = f"Auth token missing claim {claim!r}."
        raise NotAuthenticatedError(msg)
    return Principal(user_id=str(user_id), identity_source="bearer", claims=payload)


# --- oidc mode ----------------------------------------------------------------

_INVALID_TOKEN_SCHEME: str = 'Bearer error="invalid_token"'  # noqa: S105 — RFC 6750 section 3.1
_CF_CERTS_PATH: str = "/cdn-cgi/access/certs"
_CF_HEADER: str = "Cf-Access-Jwt-Assertion"
_CF_LEEWAY_SECONDS: int = 60
_INVALID_TOKEN_DETAIL: str = "invalid or expired token"  # noqa: S105 — a message, not a secret


def _jwks_cache(app: FastAPI, settings: Settings) -> JwksCache:
    """The app's one JWKS cache, built on first use (closed by the lifespan)."""
    cache: JwksCache | None = getattr(app.state, "jwks_cache", None)
    if cache is None:
        cloudflare = settings.AUTH_MODE == "cloudflare-access"
        cache = JwksCache(
            jwks_url=(
                f"https://{settings.CF_ACCESS_TEAM_DOMAIN}{_CF_CERTS_PATH}"
                if cloudflare
                else settings.OIDC_JWKS_URL
            ),
            issuer=None if cloudflare else settings.OIDC_ISSUER,
            production=settings.is_production,
            ttl=settings.JWKS_CACHE_TTL_SECONDS,
            min_refetch=settings.JWKS_MIN_REFETCH_SECONDS,
            max_stale=settings.JWKS_MAX_STALE_SECONDS,
        )
        app.state.jwks_cache = cache
    return cache


def _bearer_token(request: Request) -> str | None:
    """The token of an ``Authorization: Bearer`` header, or ``None`` when there is none."""
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    token = token.strip()
    return token if scheme.lower() == "bearer" and token else None


async def _verified_claims(  # noqa: PLR0913 — one parameter per check, shared by the token modes
    request: Request,
    token: str,
    *,
    issuer: str,
    audiences: list[str],
    algorithms: list[str],
    leeway: float,
    settings: Settings,
) -> dict[str, Any]:
    """Verify ``token``; every failure is a generic 401 (the reason is logged, never the token)."""
    try:
        return await verify_token(
            token,
            cache=_jwks_cache(request.app, settings),
            issuer=issuer,
            audiences=audiences,
            algorithms=algorithms,
            leeway=leeway,
        )
    except TokenRejectedError as exc:
        logger.info("Token rejected: %s", exc)
        raise NotAuthenticatedError(_INVALID_TOKEN_DETAIL, scheme=_INVALID_TOKEN_SCHEME) from exc
    except JwksUnavailableError as exc:
        logger.warning("Signing keys unavailable; answering 503")
        raise AuthUnavailableError from exc


def _user_id(claims: dict[str, Any], claim: str) -> str:
    value = claims.get(claim)
    if not isinstance(value, str) or not value:
        logger.info("Token rejected: user claim %r missing or not a non-empty string", claim)
        raise NotAuthenticatedError(_INVALID_TOKEN_DETAIL, scheme=_INVALID_TOKEN_SCHEME)
    return value


def _roles(claims: dict[str, Any], claim: str | None) -> list[str]:
    """Roles from a list-of-strings claim or a space-separated string; anything else is none."""
    value = claims.get(claim) if claim else None
    if isinstance(value, str):
        return value.split()
    if isinstance(value, list):
        return [r for r in value if isinstance(r, str) and r]
    return []


async def _principal_from_oidc(request: Request, settings: Settings) -> Principal:
    """Resolve identity from an OIDC provider's JWT (``Authorization: Bearer``)."""
    token = _bearer_token(request)
    if token is None:
        msg = "authentication required"
        raise NotAuthenticatedError(msg)
    # Settings validation guarantees an issuer and an audience; "" fails closed on a bad build.
    claims = await _verified_claims(
        request,
        token,
        issuer=settings.OIDC_ISSUER or "",
        audiences=settings.OIDC_AUDIENCE,
        algorithms=settings.OIDC_ALGORITHMS,
        leeway=settings.OIDC_CLOCK_SKEW_SECONDS,
        settings=settings,
    )
    return Principal(
        user_id=_user_id(claims, settings.OIDC_USER_ID_CLAIM),
        identity_source="oidc",
        roles=_roles(claims, settings.OIDC_ROLES_CLAIM),
        claims=claims,
    )


# --- cloudflare-access mode -----------------------------------------------------


async def _principal_from_cloudflare_access(request: Request, settings: Settings) -> Principal:
    """Resolve identity from the Cloudflare Access application token.

    Only the ``Cf-Access-Jwt-Assertion`` header is read. The ``CF_Authorization`` cookie is
    ignored on purpose: the header is what Cloudflare adds behind the tunnel, and trusting a
    cookie would bring cross-site request forgery questions the header does not have.
    """
    token = request.headers.get(_CF_HEADER, "").strip()
    if not token:
        msg = "authentication required"
        raise NotAuthenticatedError(msg)
    claims = await _verified_claims(
        request,
        token,
        issuer=f"https://{settings.CF_ACCESS_TEAM_DOMAIN}",
        audiences=[settings.CF_ACCESS_AUD or ""],
        algorithms=["RS256"],
        leeway=_CF_LEEWAY_SECONDS,
        settings=settings,
    )
    return Principal(
        user_id=_user_id(claims, "sub"),
        identity_source="cloudflare-access",
        claims=claims,
    )
