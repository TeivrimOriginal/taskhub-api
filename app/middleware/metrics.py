"""Metrics middleware.

Records request counts and latency. The label uses the *route template*
(`/api/v1/tasks/{task_id}`) and never the raw URL: labelling by raw path would create one
time series per task id and blow up both memory and the Prometheus backend.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Match

from app.core import metrics as collectors

_SKIPPED_PATHS = {"/metrics", "/docs", "/openapi.json", "/redoc"}


class MetricsMiddleware(BaseHTTPMiddleware):
    """Expose request count and duration histograms for every route."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if path in _SKIPPED_PATHS:
            return await call_next(request)

        collectors.http_requests_in_progress.inc()
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration = time.perf_counter() - started
            await collectors.observe_request(
                request.method, _route_template(request), 500, duration
            )
            raise
        finally:
            collectors.http_requests_in_progress.dec()

        duration = time.perf_counter() - started
        await collectors.observe_request(
            request.method,
            _route_template(request),
            response.status_code,
            duration,
        )
        return response


def _route_template(request: Request) -> str:
    """Best-effort route template for low-cardinality metric labels."""
    for route in request.app.routes:
        match, _child_scope = route.matches(request.scope)
        if match is Match.FULL:
            return getattr(route, "path", request.url.path)
    return request.url.path
