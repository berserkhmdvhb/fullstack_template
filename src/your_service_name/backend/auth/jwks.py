# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
JWKS cache: the signing keys of an identity provider, fetched and kept fresh.

Responsibilities:
- Fetch a JWKS (or the one named by an OIDC discovery document) over HTTPS with explicit timeouts,
  no redirects and a size cap.
- Keep the keys for ``ttl`` seconds; concurrent cold requests make **one** fetch (single-flight).
- On an unknown ``kid`` refetch once, but at most once per ``min_refetch`` seconds across all
  requests, so a flood of random ``kid`` values cannot hammer the provider.
- Stale-if-error: when a refresh fails keep the last good keys for up to ``max_stale`` seconds;
  with no usable keys raise `JwksUnavailableError` (a 503, never "allow").
- Keys are built with PyJWT's ``PyJWK``; only ``use=sig`` (or absent) RSA and EC keys are kept.

Public API:
- `JwksCache`: ``await cache.get_key(kid)`` -> `jwt.PyJWK` or ``None`` (unknown ``kid``).
- `JwksUnavailableError`: no usable keys could be obtained.
- `key_matches_algorithm`: does a key's type (and curve) fit a JWT ``alg``?
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Final

import httpx
from jwt import PyJWK
from jwt.exceptions import PyJWTError

from your_service_name.backend.settings import jwks_url_problem

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

#: Largest JWKS or discovery body accepted.
MAX_RESPONSE_BYTES: Final[int] = 1024 * 1024
#: Overall bound on one refresh (discovery and keys), so waiters on the lock cannot queue behind a
#: provider that drips bytes.
FETCH_DEADLINE_SECONDS: Final[float] = 10.0
_TIMEOUT: Final[httpx.Timeout] = httpx.Timeout(5.0)
_OK: Final[int] = 200
_USES: Final[frozenset[str | None]] = frozenset({None, "sig"})
_CURVES: Final[dict[str, str]] = {"ES256": "P-256", "ES384": "P-384", "ES512": "P-521"}


class JwksUnavailableError(Exception):
    """No usable signing keys: the provider is unreachable and nothing fresh enough is cached."""


def key_matches_algorithm(key: PyJWK, alg: str) -> bool:
    """RSA keys serve RS*/PS*, EC keys serve ES* on the matching curve; nothing else does."""
    if alg.startswith(("RS", "PS")):
        return bool(key.key_type == "RSA")
    if alg in _CURVES:
        return bool(key.key_type == "EC" and key._jwk_data.get("crv") == _CURVES[alg])  # noqa: SLF001 — PyJWK exposes no public curve
    return False


class JwksCache:
    """Cached, rate-limited access to one provider's signing keys."""

    def __init__(  # noqa: PLR0913 — one knob per documented setting
        self,
        *,
        jwks_url: str | None,
        issuer: str | None = None,
        production: bool = True,
        ttl: float = 3600,
        min_refetch: float = 30,
        max_stale: float = 86400,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if jwks_url is None and not issuer:
            msg = "either a JWKS URL or an issuer (for discovery) is required"
            raise ValueError(msg)
        self._url = jwks_url
        self._issuer = issuer
        self._production = production
        self._ttl = ttl
        self._min_refetch = min_refetch
        self._max_stale = max_stale
        self._client = client
        self._own_client = client is None
        self._clock = clock
        self._lock = asyncio.Lock()
        self._keys: dict[str, PyJWK] = {}
        self._fetched_at: float | None = None  # last success
        self._attempted_at: float | None = None  # last attempt, success or not

    async def aclose(self) -> None:
        """Close the HTTP client when this cache created it."""
        if self._client is not None and self._own_client:
            await self._client.aclose()

    async def get_key(self, kid: str) -> PyJWK | None:
        """Return the key for ``kid``, or ``None`` when the provider does not publish it.

        Raises
        ------
        JwksUnavailableError
            No usable keys (never fetched, or stale beyond ``max_stale``) and the fetch failed.
        """
        if self._fresh() and kid in self._keys:
            return self._keys[kid]
        async with self._lock:
            now = self._clock()
            if not self._fresh() and self._may_attempt(now):
                await self._refresh(now)
            elif kid not in self._keys and self._may_attempt(now):
                await self._refresh(now)  # unknown kid: at most one refetch per interval
            self._drop_if_too_stale()
            if not self._keys:
                msg = "no usable signing keys"
                raise JwksUnavailableError(msg)
            return self._keys.get(kid)

    # -- state ------------------------------------------------------------------

    def _fresh(self) -> bool:
        return self._fetched_at is not None and self._clock() - self._fetched_at < self._ttl

    def _usable(self) -> bool:
        return self._fetched_at is not None and self._clock() - self._fetched_at < self._max_stale

    def _may_attempt(self, now: float) -> bool:
        return self._attempted_at is None or now - self._attempted_at >= self._min_refetch

    def _drop_if_too_stale(self) -> None:
        if self._fetched_at is not None and not self._usable():
            self._keys = {}

    async def _refresh(self, now: float) -> None:
        self._attempted_at = now
        try:
            async with asyncio.timeout(FETCH_DEADLINE_SECONDS):
                keys = await self._fetch()
        except Exception as exc:  # noqa: BLE001 — any failure (even RecursionError) is a failed refresh, never a 500
            # Type only: an httpx message can carry the request URL (and its query).
            logger.warning(
                "JWKS refresh failed (%s); keeping cached keys if any", type(exc).__name__
            )
            return
        self._keys = keys
        self._fetched_at = now

    # -- network ----------------------------------------------------------------

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False)
        return self._client

    async def _get_json(self, url: str) -> Any:  # noqa: ANN401 — arbitrary JSON, validated by callers
        chunks: list[bytes] = []
        size = 0
        async with self._http().stream("GET", url, follow_redirects=False) as response:
            if response.status_code != _OK:
                msg = f"unexpected status {response.status_code}"
                raise JwksUnavailableError(msg)
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    msg = "response too large"
                    raise JwksUnavailableError(msg)
                chunks.append(chunk)
        return json.loads(b"".join(chunks))

    async def _jwks_url(self) -> str:
        if self._url is not None:
            return self._url
        assert self._issuer is not None  # noqa: S101 — constructor guarantees it
        document = await self._get_json(
            self._issuer.rstrip("/") + "/.well-known/openid-configuration"
        )
        if not isinstance(document, dict) or document.get("issuer") != self._issuer:
            msg = "discovery document issuer does not match the configured issuer"
            raise JwksUnavailableError(msg)
        uri = document.get("jwks_uri")
        if not isinstance(uri, str) or jwks_url_problem(uri, production=self._production):
            msg = "discovery document has no acceptable jwks_uri"
            raise JwksUnavailableError(msg)
        self._url = uri  # discovered once, then cached
        return uri

    async def _fetch(self) -> dict[str, PyJWK]:
        document = await self._get_json(await self._jwks_url())
        raw = document.get("keys") if isinstance(document, dict) else None
        if not isinstance(raw, list):
            msg = "JWKS has no keys array"
            raise JwksUnavailableError(msg)
        keys: dict[str, PyJWK] = {}
        for item in raw:
            if not isinstance(item, dict) or item.get("use") not in _USES:
                continue
            kid = item.get("kid")
            if not isinstance(kid, str) or not kid or item.get("kty") not in {"RSA", "EC"}:
                continue
            try:
                keys[kid] = PyJWK(item)
            except (PyJWTError, ValueError, TypeError):
                logger.warning("Skipping an unusable JWKS key")
        if not keys:
            msg = "JWKS holds no usable keys"
            raise JwksUnavailableError(msg)
        return keys
