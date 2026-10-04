"""Cache key composition, the circuit breaker and the Redis circuit-breaker contract."""

from __future__ import annotations

import pytest

from app.core import cache
from app.core.cache import is_available, make_key, mark_unavailable
from app.core.constants import CACHE_PREFIX

pytestmark = pytest.mark.unit


class TestCacheKeys:
    def test_key_is_namespaced(self) -> None:
        assert make_key("stats", "abc") == f"{CACHE_PREFIX}:stats:abc"

    def test_none_parts_are_dropped(self) -> None:
        assert make_key("task", None, 7) == f"{CACHE_PREFIX}:task:7"

    def test_no_parts_yields_the_namespace(self) -> None:
        assert make_key("ping") == f"{CACHE_PREFIX}:ping"

    def test_uuids_are_stringified(self) -> None:
        import uuid

        identifier = uuid.uuid4()
        assert make_key("user", identifier) == f"{CACHE_PREFIX}:user:{identifier}"

    def test_different_namespaces_never_collide(self) -> None:
        assert make_key("task", 1) != make_key("project", 1)


class TestCircuitBreaker:
    def test_starts_open(self) -> None:
        cache._unavailable_until = 0.0
        assert is_available() is True

    def test_marking_unavailable_closes_it(self) -> None:
        cache._unavailable_until = 0.0
        mark_unavailable(duration=30.0)
        assert is_available() is False
        cache._unavailable_until = 0.0

    def test_the_cooldown_expires(self) -> None:
        cache._unavailable_until = 0.0
        mark_unavailable(duration=0.01)
        import time

        time.sleep(0.05)
        assert is_available() is True

    async def test_cache_reads_are_skipped_while_the_breaker_is_open(self) -> None:
        """A dead Redis must cost nothing, not one timeout per request."""
        cache._unavailable_until = 0.0
        mark_unavailable(duration=30.0)
        try:
            assert await cache.get_json("any-key") is None
            await cache.set_json("any-key", {"a": 1})
            assert await cache.invalidate("any-key") == 0
            assert await cache.invalidate_pattern("taskhub:*") == 0
        finally:
            cache._unavailable_until = 0.0

    async def test_corrupt_json_is_dropped_not_raised(self, requires_redis: None) -> None:
        await cache.set_json("corrupt", {"ok": True}, ttl=30)
        key = "corrupt"
        client = cache.get_redis()
        await client.set(key, "{not json", ex=30)
        assert await cache.get_json(key) is None


class TestMaskedUrl:
    def test_credentials_are_hidden(self) -> None:
        assert cache._mask_url("redis://:secret@cache:6379/0") == "redis://***@cache:6379/0"

    def test_plain_url_is_untouched(self) -> None:
        assert cache._mask_url("redis://cache:6379/0") == "redis://cache:6379/0"


@pytest.mark.parametrize(
    "path",
    ["/metrics", "/health/live", "/health/ready"],
)
def test_probes_are_never_throttled(path: str) -> None:
    from app.middleware.rate_limit import EXEMPT_PATHS

    assert path in EXEMPT_PATHS
