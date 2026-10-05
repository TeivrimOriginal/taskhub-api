"""Redis cache and rate-limit primitives.

The cache exists to keep dashboard reads off PostgreSQL. It is optional by design: with
`CACHE_ENABLED=false` (or Redis down) every helper degrades to a no-op instead of failing
the request, because a cache outage must never become an application outage.

Cache groups
------------
Every cached value belongs to a *group* (``stats``, ``project``, ``task``, ``tag``,
``user``). When a row is written, the service says which entity changed and this module
works out the keys. Before this, each service had its own private ``_invalidate()`` method
with its own hand-written list of key patterns, and the lists had drifted: a comment write
forgot ``project``, a tag write forgot ``stats``. ``GROUPS_AFFECTED_BY`` is now the one
place that knows what a write invalidates.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast

import redis.asyncio as aioredis
from pydantic import BaseModel
from redis.exceptions import RedisError

from app.core.config import Settings, get_settings
from app.core.constants import CACHE_PREFIX, CacheEntity
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


#: What has to be dropped when a row of each entity is written.
#:
#: Read this as: "a write to X can change these cached views". The dashboard counts tasks,
#: so any task write drops the stats; a project write changes the project list *and* the
#: stats, because the dashboard counts projects. Written once here, so no service can
#: forget a group.
GROUPS_AFFECTED_BY: dict[CacheEntity, frozenset[str]] = {
    CacheEntity.USER: frozenset({"user", "stats"}),
    CacheEntity.PROJECT: frozenset({"project", "stats"}),
    CacheEntity.TASK: frozenset({"task", "project", "stats"}),
    CacheEntity.COMMENT: frozenset({"task", "project", "stats"}),
    CacheEntity.TAG: frozenset({"tag", "task", "stats"}),
}


async def invalidate_entities(*entities: CacheEntity) -> int:
    """Drop every cached group that the given writes can have changed.

    Call it after a commit, and name the entity instead of the keys::

        await invalidate_entities(CacheEntity.TASK)

    Returns the number of keys removed, or 0 when the cache is off or Redis is down —
    which is the normal, non-error case here.
    """
    groups: set[str] = set()
    for entity in entities:
        groups |= GROUPS_AFFECTED_BY.get(entity, frozenset())

    removed = 0
    for group in groups:
        removed += await invalidate_pattern(f"{CACHE_PREFIX}:{group}:*")
    logger.debug(
        "cache_groups_invalidated", entities=[e.value for e in entities], groups=sorted(groups)
    )
    return removed


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


async def cached_value[ModelT](
    key: str,
    compute: Callable[[], Awaitable[ModelT]],
    *,
    schema: type[BaseModel] | None = None,
    ttl: int | None = None,
    use_cache: bool = True,
) -> ModelT:
    """Read a value from the cache, or build it and put it there.

    This is the read-through pattern, written once. A service says *what* the value is,
    not how to store it:

        overview = await cached_value(
            make_key("stats", user.id),
            lambda: self._build(user.id),
            schema=StatsOverview,
        )

    Args:
        key: the cache key. Use ``make_key`` so the namespace is spelled the same way
            everywhere.
        compute: an async function that builds the value. It runs only on a miss, so it
            may be as expensive as it likes.
        schema: the Pydantic class of the value. Pass it whenever the value is a model —
            Redis only holds JSON, so without this the function would hand back a plain
            ``dict`` on a hit and a real object on a miss. Callers should never have to
            know which of the two they got.
        ttl: how long to keep the value. ``None`` takes ``CACHE_TTL_SECONDS``.
        use_cache: pass ``False`` to force a rebuild — this is what ``?refresh=true`` does,
            and the fresh value is still written back for the next reader.

    The three outcomes are deliberate and always the same:
        * hit  -> the cached value, rebuilt into ``schema``, and `compute` never runs;
        * miss -> `compute` runs, the result is stored and returned;
        * Redis is down -> `compute` runs and nothing is stored. A cache outage costs
          speed, never correctness, and never an error.
    """
    settings = get_settings()
    can_cache = use_cache and settings.cache_enabled

    if can_cache:
        raw = await get_json(key)
        if raw is not None:
            logger.debug("cache_hit", key=key)
            # A value in Redis is always plain JSON, so it has to be rebuilt into the
            # model the caller expects. Without `schema` the caller asked for whatever
            # `compute` returns, and a dict stays a dict.
            return cast(ModelT, schema.model_validate(raw) if schema is not None else raw)

    value = await compute()

    if can_cache:
        payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        await set_json(key, payload, ttl=ttl)
    return value


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
