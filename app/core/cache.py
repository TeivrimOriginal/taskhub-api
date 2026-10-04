"""Redis cache and rate-limit primitives.

The cache exists to keep dashboard reads off PostgreSQL. It is optional by design: with
`CACHE_ENABLED=false` (or Redis down) every helper degrades to a no-op instead of failing
the request, because a cache outage must never become an application outage.
"""

from __future__ import annotations

import json
import time
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from app.core.config import Settings, get_settings
from app.core.constants import CACHE_PREFIX
from app.core.logging import get_logger

logger = get_logger(__name__)

_client: aioredis.Redis | None = None

#: How long to keep talking to Redis after it failed. Without this circuit breaker every
#: request would pay the full `socket_connect_timeout` while Redis is down.
FAILURE_COOLDOWN_SECONDS = 5.0
_unavailable_until: float = 0.0


def mark_unavailable(duration: float = FAILURE_COOLDOWN_SECONDS) -> None:
    """Open the circuit breaker: stop dialing Redis for `duration` seconds."""
    global _unavailable_until  # noqa: PLW0603
    _unavailable_until = time.monotonic() + duration
    logger.warning("cache_circuit_opened", cooldown_seconds=duration)


def is_available() -> bool:
    """`False` while the circuit breaker is open."""
    return time.monotonic() >= _unavailable_until


def get_redis(settings: Settings | None = None) -> aioredis.Redis:
    """Return the process-wide Redis client (lazy, reused across requests)."""
    global _client  # noqa: PLW0603
    if _client is None:
        resolved = settings or get_settings()
        # `redis.asyncio.from_url` ships without annotations; the ignore is scoped to
        # that one call rather than loosening the module.
        _client = aioredis.from_url(  # type: ignore[no-untyped-call]
            resolved.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
            retry_on_timeout=False,
            health_check_interval=30,
        )
        logger.info("redis_client_created", url=_mask_url(resolved.redis_url))
    return _client


async def close_redis() -> None:
    """Close the client on application shutdown."""
    global _client  # noqa: PLW0603
    if _client is not None:
        await _client.aclose() if hasattr(_client, "aclose") else await _client.close()
        logger.info("redis_client_closed")
    _client = None


def _mask_url(url: str) -> str:
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", maxsplit=1)
    _creds, host = rest.rsplit("@", maxsplit=1)
    return f"{scheme}://***@{host}"


def make_key(namespace: str, *parts: Any) -> str:
    """Compose a namespaced cache key, e.g. `taskhub:stats:<user_id>`."""
    suffix = ":".join(str(part) for part in parts if part not in (None, ""))
    return f"{CACHE_PREFIX}:{namespace}:{suffix}" if suffix else f"{CACHE_PREFIX}:{namespace}"


async def get_json(key: str) -> Any | None:
    """Read and decode a JSON value; `None` on a miss or on any Redis failure."""
    if not is_available():
        return None
    try:
        raw = await get_redis().get(key)
    except RedisError as exc:
        logger.warning("cache_read_failed", key=key, error=str(exc))
        mark_unavailable()
        return None
    if raw is None:
        logger.debug("cache_miss", key=key)
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("cache_corrupt_value_dropped", key=key)
        await invalidate(key)
        return None


async def set_json(key: str, value: Any, *, ttl: int | None = None) -> None:
    """Store a JSON value with a TTL; failures are logged and swallowed."""
    resolved_ttl = ttl if ttl is not None else get_settings().cache_ttl_seconds
    if not get_settings().cache_enabled or not is_available():
        return
    try:
        await get_redis().set(key, json.dumps(value, default=str), ex=resolved_ttl)
        logger.debug("cache_write", key=key, ttl=resolved_ttl)
    except RedisError as exc:
        logger.warning("cache_write_failed", key=key, error=str(exc))
        mark_unavailable()


async def invalidate(*keys: str) -> int:
    """Delete cache keys, ignoring Redis errors."""
    if not keys or not get_settings().cache_enabled or not is_available():
        return 0
    try:
        return int(await get_redis().delete(*keys))
    except RedisError as exc:
        logger.warning("cache_invalidate_failed", keys=keys, error=str(exc))
        mark_unavailable()
        return 0


async def invalidate_pattern(pattern: str) -> int:
    """Delete every key matching `pattern` using a non-blocking `SCAN` loop.

    `KEYS` would block the Redis event loop for the length of the keyspace, so the
    cursor-based `SCAN` is the only acceptable choice here.
    """
    if not get_settings().cache_enabled or not is_available():
        return 0
    deleted = 0
    try:
        client = get_redis()
        async for key in client.scan_iter(match=pattern, count=100):
            await client.delete(key)
            deleted += 1
    except RedisError as exc:
        logger.warning("cache_pattern_invalidate_failed", pattern=pattern, error=str(exc))
        mark_unavailable()
    return deleted


async def ping() -> bool:
    """`True` when Redis answers — used by the readiness probe.

    The probe bypasses the circuit breaker on purpose: a readiness check must report
    the current reality, not the cached opinion from a few seconds ago.
    """
    try:
        return bool(await get_redis().ping())
    except RedisError:
        mark_unavailable()
        return False
