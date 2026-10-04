"""Comment endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import CommentServiceDep, CurrentUser, PaginationDep
from app.schemas.comment import CommentCreate, CommentRead, CommentUpdate
from app.schemas.common import ErrorResponse, Message, PaginatedResponse

router = APIRouter()

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse, "description": "Not authenticated"},
    403: {"model": ErrorResponse, "description": "Not allowed on this task"},
    404: {"model": ErrorResponse, "description": "Task or comment not found"},
}


@router.get(
    "/tasks/{task_id}/comments",
    response_model=PaginatedResponse[CommentRead],
    summary="List the comments of a task",
    responses=ERROR_RESPONSES,
)
async def list_comments(
    task_id: uuid.UUID,
    service: CommentServiceDep,
    current_user: CurrentUser,
    pagination: PaginationDep,
) -> PaginatedResponse[CommentRead]:
    """Newest comments first; requires read access to the parent task."""
    items, total = await service.list_for_task(
        task_id=task_id,
        page=pagination.page,
        size=pagination.size,
        actor=current_user,
    )
    return PaginatedResponse[CommentRead].create(
        [CommentRead.model_validate(comment, from_attributes=True) for comment in items],
        total=total,
        page=pagination.page,
        size=pagination.size,
    )


@router.post(
    "/tasks/{task_id}/comments",
    response_model=CommentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a comment to a task",
    responses=ERROR_RESPONSES,
)
async def create_comment(
    task_id: uuid.UUID,
    payload: CommentCreate,
    service: CommentServiceDep,
    current_user: CurrentUser,
) -> CommentRead:
    """Create a comment and write an activity-log entry in the same transaction."""
    comment = await service.create(task_id=task_id, body=payload.body, actor=current_user)
    return CommentRead.model_validate(comment, from_attributes=True)


@router.patch(
    "/comments/{comment_id}",
    response_model=CommentRead,
    summary="Edit your own comment",
    responses=ERROR_RESPONSES,
)
async def update_comment(
    comment_id: uuid.UUID,
    payload: CommentUpdate,
    service: CommentServiceDep,
    current_user: CurrentUser,
) -> CommentRead:
    """Only the author (or staff) may edit a comment."""
    comment = await service.update(comment_id, body=payload.body, actor=current_user)
    return CommentRead.model_validate(comment, from_attributes=True)


@router.delete(
    "/comments/{comment_id}",
    response_model=Message,
    summary="Delete a comment",
    responses=ERROR_RESPONSES,
)
async def delete_comment(
    comment_id: uuid.UUID,
    service: CommentServiceDep,
    current_user: CurrentUser,
) -> Message:
    """Soft delete; only the author or staff may do it."""
    await service.delete(comment_id, actor=current_user)
    return Message(message="Comment deleted", detail={"comment_id": str(comment_id)})
