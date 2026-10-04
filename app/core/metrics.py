"""Prometheus collectors and the `/metrics` endpoint."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from app.core.constants import ErrorCode

if TYPE_CHECKING:
    pass

# A private registry keeps application metrics separate from the process/GC metrics that
# `prometheus_client` installs globally, which makes tests deterministic.
registry: CollectorRegistry = CollectorRegistry(auto_describe=True)

http_requests_total = Counter(
    "http_requests_total",
    "Total number of HTTP requests",
    labelnames=("method", "path", "status"),
    registry=registry,
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    labelnames=("method", "path"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=registry,
)

http_requests_in_progress = Gauge(
    "http_requests_in_progress",
    "Number of HTTP requests currently being served",
    registry=registry,
)

auth_failures_total = Counter(
    "auth_failures_total",
    "Authentication failures by reason",
    labelnames=("reason",),
    registry=registry,
)

db_queries_total = Counter(
    "db_queries_total",
    "Database queries executed",
    labelnames=("operation",),
    registry=registry,
)

celery_tasks_total = Counter(
    "celery_tasks_total",
    "Celery tasks processed",
    labelnames=("task", "state"),
    registry=registry,
)

cache_operations_total = Counter(
    "cache_operations_total",
    "Cache operations by result",
    labelnames=("operation", "result"),
    registry=registry,
)

#: Reason label for auth failures, kept next to the error codes it mirrors.
AUTH_FAILURE_REASONS: frozenset[str] = frozenset(
    {
        ErrorCode.AUTHENTICATION_ERROR.value,
        ErrorCode.PERMISSION_DENIED.value,
    }
)


def metrics_response() -> tuple[bytes, str]:
    """Render the registry in the Prometheus text exposition format."""
    return generate_latest(registry), CONTENT_TYPE_LATEST


def iter_metric_lines() -> Iterator[str]:
    """Yield the exposition one line at a time (handy in tests and debugging)."""
    payload, _content_type = metrics_response()
    yield from payload.decode("utf-8").splitlines()


async def observe_request(method: str, path: str, status: int, duration: float) -> None:
    """Record one request across all HTTP collectors."""
    http_requests_total.labels(method=method, path=path, status=str(status)).inc()
    http_request_duration_seconds.labels(method=method, path=path).observe(duration)


async def scrape() -> AsyncIterator[bytes]:  # pragma: no cover - trivial adapter
    """Async generator wrapper (unused by FastAPI, kept for ASGI-level scraping)."""
    payload, _content_type = metrics_response()
    yield payload
