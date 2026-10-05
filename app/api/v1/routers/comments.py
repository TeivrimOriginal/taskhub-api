"""Comment endpoints.

The envelope, the error blocks and the access rules are shared; only the four endpoints
below are specific to comments.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import CommentServiceDep, CurrentUser, PaginationDep
from app.api.responses import ACCESS_ERRORS, WRITE_ERRORS, build_page
from app.schemas.comment import CommentCreate, CommentRead, CommentUpdate
from app.schemas.common import Message, PaginatedResponse

router = APIRouter()


@router.get(
    "/tasks/{task_id}/comments",
    response_model=PaginatedResponse[CommentRead],
    summary="List the comments of a task",
    responses=ACCESS_ERRORS,
)
async def list_comments(
    task_id: uuid.UUID,
    service: CommentServiceDep,
    current_user: CurrentUser,
    pagination: PaginationDep,
) -> PaginatedResponse[CommentRead]:
    """Newest comments first.

    Knowing a task id is not enough: the service checks that the caller may read the task
    before it returns anything.
    """
    items, total = await service.list_for_task(
        task_id=task_id,
        actor=current_user,
        **pagination.as_kwargs(),
    )
    return build_page(items, total, pagination, CommentRead)


@router.post(
    "/tasks/{task_id}/comments",
    response_model=CommentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a comment to a task",
    responses=WRITE_ERRORS,
)
async def create_comment(
    task_id: uuid.UUID,
    payload: CommentCreate,
    service: CommentServiceDep,
    current_user: CurrentUser,
) -> CommentRead:
    """Create a comment and an audit-log entry in the same transaction."""
    comment = await service.create(task_id=task_id, body=payload.body, actor=current_user)
    return CommentRead.model_validate(comment, from_attributes=True)


@router.patch(
    "/comments/{comment_id}",
    response_model=CommentRead,
    summary="Edit your own comment",
    responses=WRITE_ERRORS,
)
async def update_comment(
    comment_id: uuid.UUID,
    payload: CommentUpdate,
    service: CommentServiceDep,
    current_user: CurrentUser,
) -> CommentRead:
    """Only the author, or a staff member, may edit a comment."""
    comment = await service.update(comment_id, body=payload.body, actor=current_user)
    return CommentRead.model_validate(comment, from_attributes=True)


@router.delete(
    "/comments/{comment_id}",
    response_model=Message,
    summary="Delete a comment",
    responses=WRITE_ERRORS,
)
async def delete_comment(
    comment_id: uuid.UUID,
    service: CommentServiceDep,
    current_user: CurrentUser,
) -> Message:
    """Soft delete: the comment disappears, the audit trail does not."""
    await service.delete(comment_id, actor=current_user)
    return Message(message="Comment deleted", detail={"comment_id": str(comment_id)})
