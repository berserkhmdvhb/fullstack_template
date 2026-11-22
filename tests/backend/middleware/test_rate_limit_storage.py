# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The async limiter: keys, the storage factory, and failing open when the storage misbehaves."""

from __future__ import annotations

import asyncio
import logging
import sys
import time

import httpx
import pytest
from fastapi import FastAPI
from limits import parse
from limits.aio.storage import MemoryStorage, RedisStorage
from pydantic import SecretStr

from your_service_name.backend.middleware.rate_limiting import (
    RateLimitMiddleware,
    register_rate_limiting,
)
from your_service_name.backend.rate_limiter import RateLimiter, build_storage
from your_service_name.backend.settings import Settings

SECRET_URL = "redis://:fake-password-not-real@redis.invalid:6379/0"  # noqa: S105
PREFIX = "svc"


class _Raising(MemoryStorage):
    async def incr(self, key: str, expiry: float, amount: int = 1) -> int:  # noqa: ARG002
        msg = f"cannot reach {SECRET_URL}"
        raise ConnectionError(msg)


class _Stalling(MemoryStorage):
    async def incr(self, key: str, expiry: float, amount: int = 1) -> int:  # noqa: ARG002
        await asyncio.sleep(5)
        return 1


def _app(limiter: RateLimiter, limit: str = "2/minute") -> FastAPI:
    app = FastAPI()

    @app.get("/thing")
    def thing() -> dict[str, str]:
        return {"ok": "true"}

    app.add_middleware(RateLimitMiddleware, limit=limit, limiter=limiter, exempt_paths=frozenset())
    return app


async def _get(app: FastAPI, path: str = "/thing") -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(path)


class TestKeys:
    async def test_the_limit_is_enforced_per_client(self) -> None:
        limiter = RateLimiter(MemoryStorage(), prefix=PREFIX, timeout=1)
        item = parse("2/minute")
        assert [await limiter.hit(item, "default", "203.0.113.1") for _ in range(3)] == [
            True,
            True,
            False,
        ]
        assert await limiter.hit(item, "default", "203.0.113.2") is True

    async def test_each_namespace_counts_on_its_own(self) -> None:
        limiter = RateLimiter(MemoryStorage(), prefix=PREFIX, timeout=1)
        item = parse("1/minute")
        assert await limiter.hit(item, "default", "203.0.113.1") is True
        assert await limiter.hit(item, "default", "203.0.113.1") is False
        assert await limiter.hit(item, "csp-report", "203.0.113.1") is True

    async def test_each_limit_counts_on_its_own(self) -> None:
        limiter = RateLimiter(MemoryStorage(), prefix=PREFIX, timeout=1)
        assert await limiter.hit(parse("1/minute"), "default", "203.0.113.1") is True
        assert await limiter.hit(parse("5/minute"), "default", "203.0.113.1") is True

    async def test_keys_carry_the_service_prefix(self) -> None:
        storage = MemoryStorage()
        await RateLimiter(storage, prefix=PREFIX, timeout=1).hit(
            parse("5/minute"), "default", "203.0.113.1"
        )
        assert all(f"/{PREFIX}/default/203.0.113.1/" in key for key in storage.storage)
        assert storage.storage


class TestMiddleware:
    async def test_over_the_limit_is_a_429_problem(self) -> None:
        app = _app(RateLimiter(MemoryStorage(), prefix=PREFIX, timeout=1))
        assert [(await _get(app)).status_code for _ in range(2)] == [200, 200]
        response = await _get(app)
        assert response.status_code == httpx.codes.TOO_MANY_REQUESTS
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.headers["retry-after"] == "60"

    async def test_register_builds_the_limiter_from_settings(self) -> None:
        app = FastAPI()
        register_rate_limiting(app, Settings(RATE_LIMIT_DEFAULT="1/minute"))
        assert isinstance(app.state.rate_limiter, RateLimiter)


class TestFailOpen:
    async def test_a_raising_storage_allows_the_request_and_warns_once(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        limiter = RateLimiter(_Raising(), prefix=PREFIX, timeout=1, warn_interval=60)
        app = _app(limiter, "1/minute")
        with caplog.at_level(logging.WARNING, logger="your_service_name.backend.rate_limiter"):
            statuses = [(await _get(app)).status_code for _ in range(4)]
        assert statuses == [200, 200, 200, 200]
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "fake-password" not in caplog.text
        assert "redis.invalid" not in caplog.text
        assert "ConnectionError" in caplog.text

    async def test_the_warning_repeats_after_the_interval(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        clock = [1000.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        limiter = RateLimiter(_Raising(), prefix=PREFIX, timeout=1, warn_interval=60)
        with caplog.at_level(logging.WARNING, logger="your_service_name.backend.rate_limiter"):
            await limiter.hit(parse("1/minute"), "default", "c")
            clock[0] += 61
            await limiter.hit(parse("1/minute"), "default", "c")
        assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2  # noqa: PLR2004

    async def test_a_stalled_storage_is_cut_off_at_the_timeout(self) -> None:
        limiter = RateLimiter(_Stalling(), prefix=PREFIX, timeout=0.05)
        app = _app(limiter)
        started = time.monotonic()
        response = await _get(app)
        assert response.status_code == httpx.codes.OK
        assert time.monotonic() - started < 1


class TestBuildStorage:
    def test_memory_is_the_async_memory_storage(self) -> None:
        assert isinstance(build_storage("memory://", 0.2), MemoryStorage)

    @pytest.mark.parametrize(
        "url",
        [
            "redis://127.0.0.1:1/0",
            "rediss://:fake-password-not-real@127.0.0.1:1/2",
            "redis+unix:///nonexistent/redis.sock?db=1",
        ],
    )
    def test_redis_builds_without_connecting_and_takes_the_timeouts(self, url: str) -> None:
        storage = build_storage(url, 0.25)
        assert isinstance(storage, RedisStorage)
        assert storage.options["socket_timeout"] == 0.25  # noqa: PLR2004
        assert storage.options["socket_connect_timeout"] == 0.25  # noqa: PLR2004

    def test_redis_uses_redis_py_not_coredis(self) -> None:
        build_storage("redis://127.0.0.1:1", 0.2)
        assert "redis.asyncio" in sys.modules
        assert "coredis" not in sys.modules

    @pytest.mark.parametrize(
        "url", ["memcached://:fake-password-not-real@h", "http://h", "mongodb://h", ""]
    )
    def test_other_schemes_are_refused_without_echoing_the_url(self, url: str) -> None:
        with pytest.raises(ValueError, match="RATE_LIMIT_STORAGE") as caught:
            build_storage(url, 0.2)
        assert "fake-password" not in str(caught.value)


class TestNoUrlLeak:
    def test_a_storage_that_cannot_be_built_never_carries_the_url(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from your_service_name.backend.main import create_app  # noqa: PLC0415

        secret = "S3cret-fake-not-real"  # noqa: S105
        settings = Settings()
        # A URL Settings accepts but the storage refuses: swap it in after validation.
        object.__setattr__(
            settings, "RATE_LIMIT_STORAGE", SecretStr(f"redis://:{secret}@127.0.0.1:6379x")
        )
        with pytest.raises(ValueError, match="could not be configured") as caught:
            create_app(settings)
        error: BaseException | None = caught.value
        while error is not None:
            assert secret not in str(error)
            assert secret not in repr(error)
            error = error.__cause__ or error.__context__
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
        assert secret not in caplog.text


class TestFailureMetric:
    @staticmethod
    def _count(reason: str) -> float:
        from prometheus_client import REGISTRY  # noqa: PLC0415

        return (
            REGISTRY.get_sample_value("rate_limit_storage_failures_total", {"reason": reason})
            or 0.0
        )

    async def test_an_error_is_counted(self) -> None:
        before = self._count("error")
        limiter = RateLimiter(_Raising(), prefix=PREFIX, timeout=1)
        await limiter.hit(parse("1/minute"), "default", "c")
        assert self._count("error") == before + 1

    async def test_a_timeout_is_counted(self) -> None:
        before = self._count("timeout")
        limiter = RateLimiter(_Stalling(), prefix=PREFIX, timeout=0.01)
        await limiter.hit(parse("1/minute"), "default", "c")
        assert self._count("timeout") == before + 1

    async def test_a_healthy_hit_counts_nothing(self) -> None:
        before = (self._count("error"), self._count("timeout"))
        await RateLimiter(MemoryStorage(), prefix=PREFIX, timeout=1).hit(
            parse("1/minute"), "default", "c"
        )
        assert (self._count("error"), self._count("timeout")) == before

    async def test_it_is_in_the_registry_the_metrics_server_serves(self) -> None:
        from prometheus_client import generate_latest  # noqa: PLC0415

        await RateLimiter(_Raising(), prefix=PREFIX, timeout=1).hit(parse("1/minute"), "d", "c")
        assert "rate_limit_storage_failures_total" in generate_latest().decode()


class TestRejectionMetric:
    @staticmethod
    def _count() -> float:
        from prometheus_client import REGISTRY  # noqa: PLC0415

        return REGISTRY.get_sample_value("rate_limit_rejections_total") or 0.0

    async def test_a_429_counts_once_and_allowed_requests_count_nothing(self) -> None:
        app = _app(RateLimiter(MemoryStorage(), prefix=PREFIX, timeout=1), "1/minute")
        before = self._count()
        assert (await _get(app)).status_code == httpx.codes.OK
        assert self._count() == before
        assert (await _get(app)).status_code == httpx.codes.TOO_MANY_REQUESTS
        assert self._count() == before + 1

    async def test_a_fail_open_request_counts_nothing(self) -> None:
        app = _app(RateLimiter(_Raising(), prefix=PREFIX, timeout=1), "1/minute")
        before = self._count()
        assert (await _get(app)).status_code == httpx.codes.OK
        assert self._count() == before
