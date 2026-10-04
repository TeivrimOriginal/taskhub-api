"""Liveness and readiness probes.

Split on purpose: liveness answers "should the orchestrator restart this process?" and
must never touch a dependency; readiness answers "should traffic be routed here?" and may
fail when the database is down.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text

from app.api.deps import SessionDep
from app.core.cache import ping as redis_ping
from app.core.config import Settings, get_settings

router = APIRouter(tags=["health"])


@router.get(
    "/health/live",
    summary="Liveness probe",
    description="Always 200 while the process is able to serve requests.",
)
async def live() -> dict[str, str]:
    return {"status": "alive"}


@router.get(
    "/health/ready",
    summary="Readiness probe",
    description="Checks PostgreSQL and Redis; 503 when a dependency is unreachable.",
    responses={503: {"description": "A dependency is unavailable"}},
)
async def ready(
    response: Response,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception as exc:
        checks["database"] = False
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable", "checks": checks, "detail": str(exc)[:200]}

    checks["redis"] = await redis_ping()
    if not checks["redis"]:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "degraded", "checks": checks}

    return {
        "status": "ready",
        "checks": checks,
        "environment": settings.environment.value,
        "version": "1.0.0",
    }
