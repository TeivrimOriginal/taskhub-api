"""Liveness and readiness probes.

Split on purpose: liveness answers "should the orchestrator restart this process?" and
must never touch a dependency; readiness answers "should traffic be routed here?" and may
fail when the database is down or its schema is missing.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app import models  # noqa: F401  imports every model so Base.metadata is complete
from app.api.deps import SessionDep
from app.core.cache import ping as redis_ping
from app.core.config import Settings, get_settings
from app.db.base import Base

router = APIRouter(tags=["health"])

#: One round trip lists the tables this connection can actually see. `current_schema()`
#: rather than `public` because the app may be pointed at a different search_path.
_TABLES_IN_SCHEMA = text(
    "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
)


async def missing_tables(session: AsyncSession) -> list[str]:
    """Tables the ORM declares that the database does not have.

    `SELECT 1` proves a socket is open, not that the schema exists. Without this check a
    container that started before its migrations ran answers the readiness probe happily
    and then 503s every request, which is the worst possible failure mode for a probe:
    traffic keeps being routed at an instance that cannot serve any of it.
    """
    present = set((await session.execute(_TABLES_IN_SCHEMA)).scalars().all())
    return sorted(set(Base.metadata.tables) - present)


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
    description=(
        "Checks that PostgreSQL answers, that its schema matches this build, and that "
        "Redis answers; 503 when any of those is not true."
    ),
    responses={503: {"description": "A dependency is unavailable or the schema is wrong"}},
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

    # The probe must never raise: an exception here would surface as a 500 and hide the
    # fact that the dependency is what is broken.
    try:
        missing = await missing_tables(session)
    except SQLAlchemyError as exc:
        checks["schema"] = False
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable", "checks": checks, "detail": str(exc)[:200]}

    checks["schema"] = not missing
    if missing:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable", "checks": checks, "missing_tables": missing}

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
