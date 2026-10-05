"""ASGI application factory.

`app.main:app` is the entry point; everything else hangs off `create_app()` so a test can
build an isolated application with its own settings.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.errors import register_exception_handlers
from app.api.v1.router import api_router
from app.api.v1.routers.health import router as health_router
from app.core.cache import close_redis
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.core.metrics import metrics_response
from app.db.session import dispose_engine
from app.middleware import MetricsMiddleware, RateLimitMiddleware, RequestContextMiddleware

logger = get_logger(__name__)

DESCRIPTION = """
Backend service for **TaskHub** — projects, tasks, tags and comments.

### Highlights
* **Async everywhere** — `AsyncSession` + `asyncpg`, Redis for cache, Celery for slow work.
* **JWT auth** — short-lived access tokens, rotating refresh tokens with reuse detection.
* **RBAC** — `user` / `moderator` / `admin`, enforced by declarative dependencies.
* **OpenAPI** — every endpoint is documented and typed; try the *Authorize* button first.

### Conventions
* Pagination envelope: `{items, total, page, size, pages, has_next, has_prev}`.
* Error envelope: `{error: {code, message, details, request_id}}` — the same for every
  failure, including pydantic validation errors.
"""

TAGS_METADATA: list[dict[str, object]] = [
    {"name": "health", "description": "Liveness and readiness probes."},
    {"name": "auth", "description": "Registration, login, token rotation and revocation."},
    {"name": "users", "description": "Account directory for moderators and admins."},
    {"name": "projects", "description": "Project CRUD and per-project task listing."},
    {"name": "tasks", "description": "Task lifecycle: create, update, complete, tag."},
    {"name": "comments", "description": "Discussion attached to tasks."},
    {"name": "tags", "description": "Shared label catalogue."},
    {"name": "stats", "description": "Cached dashboard aggregates."},
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start-up / shut-down hooks: logging, eager connection check, cleanup."""
    settings: Settings = app.state.settings
    configure_logging(level=settings.log_level, json_logs=settings.log_json)
    logger.info(
        "application_started",
        app=settings.app_name,
        environment=settings.environment.value,
        docs=f"{settings.api_v1_prefix}/../docs",
    )
    try:
        yield
    finally:
        await dispose_engine()
        await close_redis()
        logger.info("application_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured `FastAPI` instance."""
    resolved = settings or get_settings()

    app = FastAPI(
        title=resolved.app_name,
        description=DESCRIPTION,
        version="1.0.0",
        openapi_tags=TAGS_METADATA,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
        swagger_ui_parameters={"persistAuthorization": True, "displayRequestDuration": True},
        contact={"name": "TaskHub", "url": "https://github.com/TeivrimOriginal/taskhub-api"},
        license_info={"name": "MIT"},
    )
    app.state.settings = resolved

    # Middleware runs bottom-up: the request-id middleware is added last so it wraps
    # everything, including the rate limiter's 429 responses.
    app.add_middleware(RateLimitMiddleware, settings=resolved)
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
    )
    if resolved.allowed_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=resolved.allowed_hosts)
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(api_router, prefix=resolved.api_v1_prefix)

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        """Prometheus exposition endpoint (scrape target).

        Returns an explicit `Response` rather than a tuple: FastAPI would otherwise try
        to JSON-encode the tuple and the scraper would receive `["# HELP ...", ...]`.
        """
        payload, content_type = metrics_response()
        return Response(content=payload, media_type=content_type)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "name": resolved.app_name,
            "version": "1.0.0",
            "docs": "/docs",
            "openapi": "/openapi.json",
            "health": "/health/ready",
        }

    return app


app = create_app()
