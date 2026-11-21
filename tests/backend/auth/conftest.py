# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Shared fixtures for the JWKS/OIDC/Cloudflare Access tests: generated keys and a fake IdP.

Keys are generated per test session with ``cryptography``; the JWKS is served through
``httpx.MockTransport`` so nothing touches the network.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt.algorithms import ECAlgorithm, RSAAlgorithm


@dataclass
class KeyPair:
    """A generated private key, its kid and the public JWK the IdP would publish."""

    kid: str
    private: Any
    jwk: dict[str, Any]

    @property
    def private_pem(self) -> bytes:
        return bytes(
            self.private.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )

    @property
    def public_pem(self) -> bytes:
        return bytes(
            self.private.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        )


def _rsa(kid: str) -> KeyPair:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private.public_key()))
    return KeyPair(kid, private, {**jwk, "kid": kid, "use": "sig", "alg": "RS256"})


def _ec(kid: str, curve: ec.EllipticCurve | None = None) -> KeyPair:
    private = ec.generate_private_key(curve or ec.SECP256R1())
    jwk = json.loads(ECAlgorithm.to_jwk(private.public_key()))
    return KeyPair(kid, private, {**jwk, "kid": kid, "use": "sig"})


@pytest.fixture(scope="session")
def rsa_key() -> KeyPair:
    return _rsa("rsa-1")


@pytest.fixture(scope="session")
def rsa_key_2() -> KeyPair:
    return _rsa("rsa-2")


@pytest.fixture(scope="session")
def ec_key() -> KeyPair:
    return _ec("ec-1")


def sign(
    key: KeyPair, claims: dict[str, Any], *, alg: str, headers: dict[str, Any] | None = None
) -> str:
    """Sign ``claims`` with ``key``; the header carries the key's kid unless overridden."""
    header = {"kid": key.kid, **(headers or {})}
    header = {k: v for k, v in header.items() if v is not None}
    return jwt.encode(claims, key.private_pem, algorithm=alg, headers=header)


@dataclass
class FakeIdP:
    """A mock identity provider: serves a JWKS (and discovery), counts and can break requests."""

    keys: list[KeyPair]
    base: str = "https://idp.example.com"
    discovery: dict[str, Any] | None = None
    fail: bool = False
    delay: float = 0.0
    requests: list[httpx.Request] = field(default_factory=list)
    raw_body: bytes | None = None
    status: int = 200

    @property
    def jwks_url(self) -> str:
        return f"{self.base}/jwks.json"

    @property
    def jwks_requests(self) -> int:
        return sum(
            1 for r in self.requests if r.url.path.endswith("/jwks.json") or "certs" in r.url.path
        )

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            return httpx.Response(500, text="boom")
        if self.raw_body is not None:
            return httpx.Response(self.status, content=self.raw_body)
        if request.url.path.endswith("/.well-known/openid-configuration"):
            if self.discovery is None:
                return httpx.Response(404)
            return httpx.Response(200, json=self.discovery)
        return httpx.Response(self.status, json={"keys": [k.jwk for k in self.keys]})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))


@pytest.fixture
def idp(rsa_key: KeyPair, ec_key: KeyPair) -> FakeIdP:
    return FakeIdP(keys=[rsa_key, ec_key])


class FakeClock:
    """A settable monotonic clock."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()
