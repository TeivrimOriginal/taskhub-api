"""Tag endpoints.

Shared pieces: `tag_filters()` for the query string, `build_page()` for the envelope and
`ACCESS_ERRORS` / `WRITE_ERRORS` for the OpenAPI error blocks.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, PaginationDep, RequireStaff, TagServiceDep
from app.api.filters import FilterSet, tag_filters
from app.api.responses import ACCESS_ERRORS, WRITE_ERRORS, build_page, to_schema
from app.schemas.common import Message, PaginatedResponse
from app.schemas.tag import TagCreate, TagRead, TagUpdate

router = APIRouter()

TagFilters = Annotated[FilterSet, Depends(tag_filters)]

#: How many rows "most used" returns when the caller does not say.
DEFAULT_POPULAR_LIMIT = 10


@router.post(
    "",
    response_model=TagRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a tag (reusing an identical name)",
    responses=ACCESS_ERRORS,
)
async def create_tag(
    payload: TagCreate,
    service: TagServiceDep,
    current_user: CurrentUser,
) -> TagRead:
    """Idempotent create: an existing tag with the same name is returned instead."""
    tag = await service.create(payload, creator=current_user)
    return to_schema(tag, TagRead)


@router.get(
    "",
    response_model=PaginatedResponse[TagRead],
    summary="List tags",
    responses=ACCESS_ERRORS,
)
async def list_tags(
    service: TagServiceDep,
    _user: CurrentUser,
    pagination: PaginationDep,
    filters: TagFilters,
) -> PaginatedResponse[TagRead]:
    """The tag catalogue, in alphabetical order."""
    items, total = await service.list_tags(**pagination.as_kwargs(), **filters.to_repository())
    return build_page(items, total, pagination, TagRead)


@router.get(
    "/popular",
    summary="Most used tags (staff only)",
    responses=ACCESS_ERRORS,
)
async def popular_tags(
    service: TagServiceDep,
    _staff: RequireStaff,
    limit: Annotated[int, Query(ge=1, le=50, description="How many tags to return")] = (
        DEFAULT_POPULAR_LIMIT
    ),
) -> list[dict[str, object]]:
    """Tags ordered by the number of live tasks that carry them."""
    pairs = await service.most_used(limit=limit)
    return [
        {"id": str(tag.id), "name": tag.name, "color": tag.color, "usage": usage}
        for tag, usage in pairs
    ]


@router.patch(
    "/{tag_id}",
    response_model=TagRead,
    summary="Rename or recolour a tag (staff only)",
    responses=WRITE_ERRORS,
)
async def update_tag(
    tag_id: uuid.UUID,
    payload: TagUpdate,
    service: TagServiceDep,
    actor: RequireStaff,
) -> TagRead:
    """Rename a label everywhere it is used."""
    values = payload.model_dump(exclude_unset=True, exclude_none=True)
    tag = await service.update(tag_id, values=values, actor=actor)
    return to_schema(tag, TagRead)


@router.delete(
    "/{tag_id}",
    response_model=Message,
    summary="Delete a tag (staff only)",
    responses=WRITE_ERRORS,
)
async def delete_tag(tag_id: uuid.UUID, service: TagServiceDep, actor: RequireStaff) -> Message:
    """Remove the label; task links disappear in the database."""
    await service.delete(tag_id, actor=actor)
    return Message(message="Tag deleted", detail={"tag_id": str(tag_id)})
