"""Request-id and access-log middleware.

Assigns (or honours an incoming) `X-Request-ID`, binds it to the structlog context so
every log line of that request carries it, and emits exactly one access log entry with
method, path, status and duration.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import bind_request_context, clear_request_context, get_logger

REQUEST_ID_HEADER = "X-Request-ID"
logger = get_logger("app.request")

# Endpoints that would drown the log in noise if they were logged per request.
QUIET_PATHS = {"/metrics", "/health/live", "/health/ready"}


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Propagate a correlation id and log one line per request."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        request.state.request_id = request_id

        clear_request_context()
        bind_request_context(request_id=request_id)

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.exception(
                "request_failed",
                method=request.method,
                path=request.url.path,
                duration_ms=duration_ms,
            )
            raise
        finally:
            clear_request_context()

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers["X-Response-Time-Ms"] = str(duration_ms)

        if request.url.path not in QUIET_PATHS:
            logger.info(
                "request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=duration_ms,
                client=request.client.host if request.client else None,
                user_id=getattr(request.state, "user_id", None),
            )
        return response
