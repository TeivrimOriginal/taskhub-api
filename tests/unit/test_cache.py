"""Cache keys, the circuit breaker, the read-through helper and the cache contract.

`app.core.cache` is the one module every cached read goes through, so these tests cover
the whole contract a caller can rely on: a hit skips the database, a miss builds the
value, and a dead Redis never turns into a failed request.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest

from app.core import cache
from app.core.cache import cached_value, is_available, make_key, mark_unavailable
from app.core.config import get_settings
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


class TestCachedValue:
    """The read-through helper every cached read goes through.

    The contract that matters: `compute` runs when the value is missing, and a broken
    cache never stops it from running.

    The suite runs with `CACHE_ENABLED=false`, so each test switches the cache on for
    itself with the `cache_on` fixture. A side benefit: these tests give the same answer
    whether or not a Redis happens to be running on the machine.
    """

    async def test_a_miss_builds_the_value(self, cache_on: None) -> None:
        calls = []

        async def compute() -> dict[str, int]:
            calls.append(1)
            return {"projects": 3}

        with patch.object(cache, "get_json", _never_cached):
            assert await cached_value("k", compute) == {"projects": 3}
        assert calls == [1], "a miss must build the value exactly once"

    async def test_a_hit_skips_the_build(self, cache_on: None) -> None:
        """The whole point of a cache: the database is not touched on a hit."""
        calls = []

        async def compute() -> dict[str, int]:
            calls.append(1)
            return {"projects": 0}

        with patch.object(cache, "get_json", _always_cached({"projects": 9})):
            assert await cached_value("k", compute) == {"projects": 9}
        assert calls == [], "the database must not be touched on a cache hit"

    async def test_use_cache_false_forces_a_rebuild(self, cache_on: None) -> None:
        """This is exactly what the `?refresh=true` query parameter does."""
        calls = []

        async def compute() -> dict[str, int]:
            calls.append(1)
            return {"projects": 1}

        with patch.object(cache, "get_json", _always_cached({"projects": 9})):
            result = await cached_value("k", compute, use_cache=False)
        assert result == {"projects": 1}
        assert calls == [1]

    async def test_the_cache_switched_off_is_never_touched(self) -> None:
        """`CACHE_ENABLED=false` must skip the cache completely, not half-use it."""
        stored: list[str] = []

        async def fake_set(key: str, value: object, *, ttl: int | None = None) -> None:
            stored.append(key)

        with (
            patch.object(cache, "get_json", _never_cached),
            patch.object(cache, "set_json", fake_set),
        ):
            assert await cached_value("k", _returns("built")) == "built"
        assert stored == [], "nothing may be stored while the cache is off"

    async def test_a_broken_cache_still_builds_the_value(self, cache_on: None) -> None:
        """Redis being down may cost speed, never the answer and never an error."""
        cache._unavailable_until = 0.0
        mark_unavailable(duration=30.0)
        try:
            assert await cached_value("k", _returns("computed anyway")) == "computed anyway"
        finally:
            cache._unavailable_until = 0.0

    async def test_a_schema_object_is_stored_as_json(self, cache_on: None) -> None:
        """A Pydantic model is dumped, so the value that lands in Redis is plain JSON."""
        from pydantic import BaseModel

        class Answer(BaseModel):
            total: int

        async def compute() -> Answer:
            return Answer(total=4)

        stored: list[tuple[str, object]] = []

        async def fake_set(key: str, value: object, *, ttl: int | None = None) -> None:
            stored.append((key, value))

        with (
            patch.object(cache, "get_json", _never_cached),
            patch.object(cache, "set_json", fake_set),
        ):
            await cached_value("k", compute, schema=Answer)
        assert stored == [("k", {"total": 4})]

    async def test_a_hit_returns_a_model_and_not_a_plain_dict(self, cache_on: None) -> None:
        """The bug this test exists for.

        Redis only holds JSON. Without `schema=`, a hit would hand back a `dict` while a
        miss returned a real object — the same method returning two different types
        depending on invisible state.
        """
        from pydantic import BaseModel

        class Answer(BaseModel):
            total: int

        async def compute() -> Answer:
            return Answer(total=4)

        with patch.object(cache, "get_json", _always_cached({"total": 9})):
            value = await cached_value("k", compute, schema=Answer)
        assert isinstance(value, Answer), "a hit must give the same type as a miss"
        assert value.total == 9


@pytest.fixture
def cache_on() -> Iterator[None]:
    """Switch the cache on for one test, and put the real settings back after.

    `get_settings` is cached, so flipping one attribute is enough — and the test does not
    have to reload the whole configuration to do it.
    """
    settings = get_settings()
    original = settings.cache_enabled
    settings.cache_enabled = True
    try:
        yield
    finally:
        settings.cache_enabled = original
        cache._unavailable_until = 0.0


async def _never_cached(key: str) -> None:
    """Stand-in for `get_json` when nothing is in the cache."""
    return


def _always_cached(value: object) -> Any:
    """Build a stand-in for `get_json` that always finds `value`."""

    async def _get(key: str) -> object:
        return value

    return _get


def _returns(value: object) -> Any:
    """Build a `compute` function that always answers `value`."""

    async def _compute() -> object:
        return value

    return _compute


@pytest.mark.parametrize(
    "path",
    ["/metrics", "/health/live", "/health/ready"],
)
def test_probes_are_never_throttled(path: str) -> None:
    from app.middleware.rate_limit import EXEMPT_PATHS

    assert path in EXEMPT_PATHS
