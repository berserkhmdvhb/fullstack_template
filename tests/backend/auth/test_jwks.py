# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Tests for the JWKS cache (``backend.auth.jwks``): caching, rate limit, staleness."""

from __future__ import annotations

import asyncio

import pytest
from jwt import PyJWK

from tests.backend.auth.conftest import FakeClock, FakeIdP, KeyPair
from your_service_name.backend.auth import jwks
from your_service_name.backend.auth.jwks import (
    MAX_RESPONSE_BYTES,
    JwksCache,
    JwksUnavailableError,
    key_matches_algorithm,
)
from your_service_name.backend.settings import jwks_url_problem

TTL, MIN_REFETCH, MAX_STALE = 100, 30, 1000
TWICE, THRICE = 2, 3


def _cache(idp: FakeIdP, clock: FakeClock, **kw: object) -> JwksCache:
    params: dict[str, object] = {
        "jwks_url": idp.jwks_url,
        "ttl": TTL,
        "min_refetch": MIN_REFETCH,
        "max_stale": MAX_STALE,
        "client": idp.client(),
        "clock": clock,
    }
    return JwksCache(**{**params, **kw})  # type: ignore[arg-type]


async def test_known_kid_is_cached_within_ttl(
    idp: FakeIdP, clock: FakeClock, rsa_key: KeyPair
) -> None:
    cache = _cache(idp, clock)
    assert await cache.get_key(rsa_key.kid) is not None
    clock.advance(TTL - 1)
    assert await cache.get_key(rsa_key.kid) is not None
    assert idp.jwks_requests == 1


async def test_refreshed_after_ttl(idp: FakeIdP, clock: FakeClock, rsa_key: KeyPair) -> None:
    cache = _cache(idp, clock)
    await cache.get_key(rsa_key.kid)
    clock.advance(TTL + 1)
    await cache.get_key(rsa_key.kid)
    assert idp.jwks_requests == TWICE


async def test_single_flight_concurrent_cold_requests_make_one_fetch(
    idp: FakeIdP, clock: FakeClock, rsa_key: KeyPair
) -> None:
    idp.delay = 0.05
    cache = _cache(idp, clock)
    results = await asyncio.gather(*(cache.get_key(rsa_key.kid) for _ in range(20)))
    assert all(r is not None for r in results)
    assert idp.jwks_requests == 1


async def test_unknown_kid_refetches_once_then_none(
    idp: FakeIdP, clock: FakeClock, rsa_key: KeyPair
) -> None:
    cache = _cache(idp, clock)
    await cache.get_key(rsa_key.kid)
    clock.advance(MIN_REFETCH + 1)
    assert await cache.get_key("nope") is None
    assert idp.jwks_requests == TWICE


async def test_unknown_kid_picks_up_a_rotated_in_key(
    idp: FakeIdP, clock: FakeClock, rsa_key: KeyPair, rsa_key_2: KeyPair
) -> None:
    cache = _cache(idp, clock)
    await cache.get_key(rsa_key.kid)
    idp.keys.append(rsa_key_2)
    clock.advance(MIN_REFETCH + 1)
    assert await cache.get_key(rsa_key_2.kid) is not None


async def test_refetch_rate_limit_flood_of_unknown_kids(
    idp: FakeIdP, clock: FakeClock, rsa_key: KeyPair
) -> None:
    cache = _cache(idp, clock)
    await cache.get_key(rsa_key.kid)  # the cold fetch
    clock.advance(MIN_REFETCH + 1)
    for i in range(50):
        assert await cache.get_key(f"random-{i}") is None
    assert idp.jwks_requests == TWICE  # one refetch for the whole flood
    clock.advance(MIN_REFETCH + 1)
    assert await cache.get_key("another") is None
    assert idp.jwks_requests == THRICE  # and one more per interval


async def test_stale_if_error_keeps_last_good_keys(
    idp: FakeIdP, clock: FakeClock, rsa_key: KeyPair
) -> None:
    cache = _cache(idp, clock)
    await cache.get_key(rsa_key.kid)
    idp.fail = True
    clock.advance(TTL + 1)
    assert await cache.get_key(rsa_key.kid) is not None  # served stale
    assert idp.jwks_requests == TWICE


async def test_503_after_max_staleness(idp: FakeIdP, clock: FakeClock, rsa_key: KeyPair) -> None:
    cache = _cache(idp, clock)
    await cache.get_key(rsa_key.kid)
    idp.fail = True
    clock.advance(MAX_STALE + 1)
    with pytest.raises(JwksUnavailableError):
        await cache.get_key(rsa_key.kid)


async def test_503_when_cold_and_provider_down(
    idp: FakeIdP, clock: FakeClock, rsa_key: KeyPair
) -> None:
    idp.fail = True
    cache = _cache(idp, clock)
    with pytest.raises(JwksUnavailableError):
        await cache.get_key(rsa_key.kid)
    idp.fail = False
    clock.advance(MIN_REFETCH + 1)
    assert await cache.get_key(rsa_key.kid) is not None  # recovers


async def test_failed_refresh_is_rate_limited(
    idp: FakeIdP, clock: FakeClock, rsa_key: KeyPair
) -> None:
    idp.fail = True
    cache = _cache(idp, clock)
    for _ in range(10):
        with pytest.raises(JwksUnavailableError):
            await cache.get_key(rsa_key.kid)
    assert idp.jwks_requests == 1


async def test_warning_does_not_log_the_url(
    idp: FakeIdP, clock: FakeClock, rsa_key: KeyPair, caplog: pytest.LogCaptureFixture
) -> None:
    idp.fail = True
    cache = _cache(idp, clock, jwks_url=idp.jwks_url + "?secret=abc")
    with pytest.raises(JwksUnavailableError):
        await cache.get_key(rsa_key.kid)
    ours = " ".join(
        r.getMessage() for r in caplog.records if r.name == "your_service_name.backend.auth.jwks"
    )
    assert "refresh failed" in ours
    assert "abc" not in ours


async def test_oversized_response_is_refused(
    idp: FakeIdP, clock: FakeClock, rsa_key: KeyPair
) -> None:
    idp.raw_body = b"x" * (MAX_RESPONSE_BYTES + 1)
    with pytest.raises(JwksUnavailableError):
        await _cache(idp, clock).get_key(rsa_key.kid)


async def test_redirect_is_not_followed(idp: FakeIdP, clock: FakeClock, rsa_key: KeyPair) -> None:
    target = "https://idp.example.com/elsewhere/jwks.json"  # would serve a valid JWKS if followed
    idp.redirect_to = target
    with pytest.raises(JwksUnavailableError):
        await _cache(idp, clock).get_key(rsa_key.kid)
    assert [str(r.url) for r in idp.requests] == [idp.jwks_url]  # the Location was never fetched


async def test_one_bad_key_does_not_discard_the_others(
    idp: FakeIdP, clock: FakeClock, rsa_key: KeyPair
) -> None:
    bad_n = {**rsa_key.jwk, "kid": "bad-n", "n": 12345}  # not a string
    bad_e = {**rsa_key.jwk, "kid": "bad-e", "e": "!!!"}
    bad_xy = {"kty": "EC", "crv": "P-256", "kid": "bad-xy", "x": 1, "y": None}
    idp.keys = [
        KeyPair("bad-n", None, bad_n),
        KeyPair("bad-e", None, bad_e),
        KeyPair("bad-xy", None, bad_xy),
        rsa_key,
    ]
    cache = _cache(idp, clock)
    assert await cache.get_key(rsa_key.kid) is not None
    for kid in ("bad-n", "bad-e", "bad-xy"):
        assert await cache.get_key(kid) is None


async def test_a_deeply_nested_body_is_a_failed_refresh(
    idp: FakeIdP, clock: FakeClock, rsa_key: KeyPair
) -> None:
    idp.raw_body = b"[" * 100_000 + b"]" * 100_000
    with pytest.raises(JwksUnavailableError):
        await _cache(idp, clock).get_key(rsa_key.kid)


async def test_a_deeply_nested_body_keeps_stale_keys(
    idp: FakeIdP, clock: FakeClock, rsa_key: KeyPair
) -> None:
    cache = _cache(idp, clock)
    await cache.get_key(rsa_key.kid)
    idp.raw_body = b"[" * 100_000 + b"]" * 100_000
    clock.advance(TTL + 1)
    assert await cache.get_key(rsa_key.kid) is not None


async def test_a_stalled_provider_is_cut_off_by_the_overall_deadline(
    idp: FakeIdP, clock: FakeClock, rsa_key: KeyPair, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jwks, "FETCH_DEADLINE_SECONDS", 0.2)
    idp.stall = True
    cache = _cache(idp, clock)
    waiters = [asyncio.create_task(cache.get_key(rsa_key.kid)) for _ in range(5)]
    results = await asyncio.wait_for(asyncio.gather(*waiters, return_exceptions=True), timeout=5)
    assert all(isinstance(r, JwksUnavailableError) for r in results)
    assert idp.jwks_requests == 1


async def test_non_signing_and_unsupported_keys_are_dropped(
    idp: FakeIdP, clock: FakeClock, rsa_key: KeyPair, ec_key: KeyPair
) -> None:
    enc = {**ec_key.jwk, "kid": "enc-1", "use": "enc"}
    okp = {"kty": "OKP", "crv": "Ed25519", "x": "AAAA", "kid": "okp-1"}
    nokid = {k: v for k, v in ec_key.jwk.items() if k != "kid"}
    idp.keys = [
        rsa_key,
        KeyPair("enc-1", None, enc),
        KeyPair("okp-1", None, okp),
        KeyPair("", None, nokid),
    ]
    cache = _cache(idp, clock)
    assert await cache.get_key(rsa_key.kid) is not None
    clock.advance(MIN_REFETCH + 1)
    assert await cache.get_key("enc-1") is None
    assert await cache.get_key("okp-1") is None


def test_key_matches_algorithm(rsa_key: KeyPair, ec_key: KeyPair) -> None:
    rsa_jwk, ec_jwk = PyJWK(rsa_key.jwk), PyJWK(ec_key.jwk)
    assert key_matches_algorithm(rsa_jwk, "RS256")
    assert not key_matches_algorithm(rsa_jwk, "ES256")
    assert key_matches_algorithm(ec_jwk, "ES256")
    assert not key_matches_algorithm(ec_jwk, "RS256")
    assert not key_matches_algorithm(ec_jwk, "ES384")  # wrong curve
    assert not key_matches_algorithm(rsa_jwk, "HS256")
    assert not key_matches_algorithm(rsa_jwk, "none")


# --- discovery ----------------------------------------------------------------


async def test_discovery_fetches_jwks_uri_once_and_caches_it(
    idp: FakeIdP, clock: FakeClock, rsa_key: KeyPair
) -> None:
    idp.discovery = {"issuer": idp.base, "jwks_uri": idp.jwks_url}
    cache = _cache(idp, clock, jwks_url=None, issuer=idp.base)
    await cache.get_key(rsa_key.kid)
    clock.advance(TTL + 1)
    await cache.get_key(rsa_key.kid)
    discovery = [r for r in idp.requests if r.url.path.endswith("openid-configuration")]
    assert len(discovery) == 1
    assert idp.jwks_requests == TWICE


async def test_discovery_issuer_mismatch_is_refused(
    idp: FakeIdP, clock: FakeClock, rsa_key: KeyPair
) -> None:
    idp.discovery = {"issuer": "https://evil.example.com", "jwks_uri": idp.jwks_url}
    cache = _cache(idp, clock, jwks_url=None, issuer=idp.base)
    with pytest.raises(JwksUnavailableError):
        await cache.get_key(rsa_key.kid)
    assert idp.jwks_requests == 0


async def test_discovered_http_jwks_uri_is_refused_in_production(
    idp: FakeIdP, clock: FakeClock, rsa_key: KeyPair
) -> None:
    idp.discovery = {"issuer": idp.base, "jwks_uri": "http://localhost/jwks.json"}
    cache = _cache(idp, clock, jwks_url=None, issuer=idp.base, production=True)
    with pytest.raises(JwksUnavailableError):
        await cache.get_key(rsa_key.kid)


# --- URL check ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "production", "ok"),
    [
        ("https://idp.example.com/jwks", True, True),
        ("https://idp.example.com/jwks", False, True),
        ("http://idp.example.com/jwks", False, False),
        ("http://localhost:9000/jwks", False, True),
        ("http://127.0.0.1/jwks", False, True),
        ("http://localhost:9000/jwks", True, False),
        ("ftp://idp.example.com/jwks", False, False),
        ("idp.example.com/jwks", False, False),
        ("https:///jwks", False, False),
    ],
)
def test_jwks_url_check(url: str, production: bool, ok: bool) -> None:  # noqa: FBT001
    assert (jwks_url_problem(url, production=production) is None) is ok
