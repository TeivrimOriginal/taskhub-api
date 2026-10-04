"""Rate limiting middleware (Redis fixed window).

A fixed window costs two Redis commands (`INCR` + `EXPIRE`) and no Lua. A sliding window
would be smoother but needs a sorted set; for the abuse-prevention use case here the
difference does not matter, and `429`s are the point.

Redis being down must not break the API, so a failure degrades to "not limited".
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.cache import get_redis, is_available, mark_unavailable
from app.core.config import Settings, get_settings
from app.core.constants import KEY_RATE_LIMIT, ErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Paths that are never throttled — health probes and docs must stay reachable.
EXEMPT_PATHS = {
    "/metrics",
    "/health/live",
    "/health/ready",
    "/docs",
    "/redoc",
    "/openapi.json",
}

RATE_LIMIT_HEADERS = {
    "X-RateLimit-Limit": "limit",
    "X-RateLimit-Remaining": "remaining",
    "X-RateLimit-Reset": "reset",
}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Limit each client to `requests` per `window_seconds`."""

    def __init__(self, app: object, settings: Settings | None = None) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.settings = settings or get_settings()

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not self.settings.rate_limit_enabled or request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        limit = self.settings.rate_limit_requests
        window = self.settings.rate_limit_window_seconds
        client = self._client_key(request)
        key = f"{KEY_RATE_LIMIT}:{client}:{int(time.time()) // window}"

        allowed, remaining, reset_after = await self._consume(key, limit=limit, window=window)
        if not allowed:
            logger.warning("rate_limit_exceeded", client=client, path=request.url.path)
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": ErrorCode.RATE_LIMITED.value,
                        "message": "Too many requests, slow down",
                        "details": {"limit": limit, "window_seconds": window},
                        "request_id": getattr(request.state, "request_id", None),
                    }
                },
                headers={
                    "Retry-After": str(reset_after),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_after),
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_after)
        return response

    @staticmethod
    async def _consume(key: str, *, limit: int, window: int) -> tuple[bool, int, int]:
        """Increment the window counter; returns `(allowed, remaining, reset_after)`."""
        if not is_available():
            # Circuit breaker open: fail open rather than dial a dead socket per request.
            return True, limit, window
        try:
            client = get_redis()
            pipe = client.pipeline()
            pipe.incr(key)
            pipe.ttl(key)
            counter, ttl = await pipe.execute()
        except RedisError as exc:
            logger.warning("rate_limit_backend_unavailable", error=str(exc))
            mark_unavailable()
            return True, limit, window

        if ttl is None or ttl < 0:
            # First request in this window: set the expiry so the key cannot live forever.
            await get_redis().expire(key, window)
            ttl = window

        used = int(counter)
        return used <= limit, max(limit - used, 0), int(ttl)

    @staticmethod
    def _client_key(request: Request) -> str:
        """Identify the caller.

        `X-Forwarded-For` is honoured because the app normally sits behind Nginx; the
        left-most entry is the original client.
        """
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"
