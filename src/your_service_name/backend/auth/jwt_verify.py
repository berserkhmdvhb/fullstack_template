# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Verify a JWT against a JWKS: the checks the ``oidc`` mode relies on.

Responsibilities:
- Refuse oversized tokens, an ``alg`` outside the allowlist (so ``none`` and HS* never get through),
  a missing ``kid``, a non-empty ``crit`` header and a key whose type does not fit the ``alg``.
- Let PyJWT check the signature, ``iss`` (exact), ``aud`` (intersects), ``exp`` (required) and
  ``nbf``/``iat`` (when present), all with the configured leeway.

Public API:
- `verify_token`: returns the verified claims, or raises.
- `TokenRejectedError`: the token is not acceptable (the reason is for the log, never the client).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

import jwt

from your_service_name.backend.auth.jwks import key_matches_algorithm

if TYPE_CHECKING:
    from collections.abc import Sequence

    from your_service_name.backend.auth.jwks import JwksCache

#: Largest token accepted, in bytes.
MAX_TOKEN_BYTES: Final[int] = 8 * 1024


class TokenRejectedError(Exception):
    """The token is not acceptable; the message is a reason for the log (it never has the token)."""


async def verify_token(  # noqa: PLR0913 — the checks are the parameters
    token: str,
    *,
    cache: JwksCache,
    issuer: str,
    audiences: Sequence[str],
    algorithms: Sequence[str],
    leeway: float,
) -> dict[str, Any]:
    """Verify ``token`` and return its claims.

    Raises
    ------
    TokenRejectedError
        Any check failed.
    JwksUnavailableError
        The signing keys could not be obtained (the caller answers 503).
    """
    if len(token) > MAX_TOKEN_BYTES:
        msg = "token too large"
        raise TokenRejectedError(msg)
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        msg = "malformed token"
        raise TokenRejectedError(msg) from exc
    alg = header.get("alg")
    if not isinstance(alg, str) or alg not in algorithms:
        msg = "algorithm not allowed"
        raise TokenRejectedError(msg)
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        msg = "missing kid"
        raise TokenRejectedError(msg)
    if header.get("crit"):
        msg = "crit header refused"
        raise TokenRejectedError(msg)
    key = await cache.get_key(kid)
    if key is None:
        msg = "unknown kid"
        raise TokenRejectedError(msg)
    if not key_matches_algorithm(key, alg):
        msg = "key type does not match alg"
        raise TokenRejectedError(msg)
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            key.key,
            algorithms=[alg],
            issuer=issuer,
            audience=list(audiences),
            leeway=leeway,
            options={"require": ["exp", "iss", "aud"]},
        )
    except jwt.PyJWTError as exc:
        msg = f"{type(exc).__name__}: {exc}"
        raise TokenRejectedError(msg) from exc
    return claims
