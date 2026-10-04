"""Dashboard statistics endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, StatsServiceDep
from app.core.constants import ErrorCode
from app.schemas.common import ErrorResponse
from app.schemas.stats import StatsOverview

router = APIRouter()


@router.get(
    "/overview",
    response_model=StatsOverview,
    summary="Aggregated dashboard of the current user",
    responses={
        401: {"model": ErrorResponse, "description": ErrorCode.AUTHENTICATION_ERROR.value},
    },
)
async def overview(
    service: StatsServiceDep,
    current_user: CurrentUser,
    refresh: Annotated[bool, Query(description="Bypass the cache")] = False,
) -> StatsOverview:
    """Project/task counters, status and priority breakdowns.

    The result is cached in Redis; `?refresh=true` forces a recomputation, which is what
    the integration tests use to assert cache invalidation after a write.
    """
    return await service.overview(user=current_user, use_cache=not refresh)


@router.get(
    "/velocity",
    summary="Time-to-completion for the current user",
    responses={
        401: {"model": ErrorResponse, "description": ErrorCode.AUTHENTICATION_ERROR.value},
    },
)
async def velocity(
    service: StatsServiceDep, current_user: CurrentUser
) -> dict[str, float | int | None]:
    """Average hours from creation to completion, plus the open assigned count."""
    average = await service.average_completion_hours(current_user.id)
    assigned = await service.count_assigned_to_me(current_user.id)
    return {
        "average_completion_hours": average,
        "assigned_open_tasks": assigned,
    }
