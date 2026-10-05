"""Task endpoints.

The filters, the error blocks, the pagination envelope and the access rules are all shared:
`app.api.filters`, `app.api.responses`, `build_page`, `app.core.permissions`. What is left
here is only what is specific to tasks.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import CurrentUser, PaginationDep, TaskServiceDep
from app.api.filters import FilterSet, task_filters
from app.api.responses import ACCESS_ERRORS, WRITE_ERRORS, build_page
from app.schemas.common import Message, PaginatedResponse
from app.schemas.tag import TagAttach, TagRead
from app.schemas.task import TaskCreate, TaskRead, TaskUpdate

router = APIRouter()

TaskFilters = Annotated[FilterSet, Depends(task_filters)]


@router.post(
    "",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a task",
    responses=WRITE_ERRORS,
)
async def create_task(
    payload: TaskCreate,
    service: TaskServiceDep,
    current_user: CurrentUser,
) -> TaskRead:
    """Create a task and notify the assignee through the background queue."""
    task = await service.create(payload, actor=current_user)
    return TaskRead.model_validate(task, from_attributes=True)


@router.get(
    "",
    response_model=PaginatedResponse[TaskRead],
    summary="List tasks",
    responses=ACCESS_ERRORS,
)
async def list_tasks(
    service: TaskServiceDep,
    current_user: CurrentUser,
    pagination: PaginationDep,
    filters: TaskFilters,
) -> PaginatedResponse[TaskRead]:
    """The one task listing: every filter is optional and they combine with AND.

    ``?mine=true`` is a rule and not a filter, so it is taken out of the set here and
    given to the service as ``mine_only``. The service then forces it to mean "me",
    whatever the caller asked for.
    """
    mine_only = filters.take("mine")
    items, total = await service.list_for_user(
        user=current_user,
        **pagination.as_kwargs(),
        mine_only=mine_only,
        **filters.to_repository(),
    )
    return build_page(items, total, pagination, TaskRead)


@router.get(
    "/{task_id}",
    response_model=TaskRead,
    summary="Read a task",
    responses=ACCESS_ERRORS,
)
async def read_task(task_id: uuid.UUID, service: TaskServiceDep, _user: CurrentUser) -> TaskRead:
    """Task detail with tags, project and assignee already loaded."""
    task = await service.get(task_id)
    return TaskRead.model_validate(task, from_attributes=True)


@router.patch(
    "/{task_id}",
    response_model=TaskRead,
    summary="Update a task",
    responses=WRITE_ERRORS,
)
async def update_task(
    task_id: uuid.UUID,
    payload: TaskUpdate,
    service: TaskServiceDep,
    current_user: CurrentUser,
) -> TaskRead:
    """Partial update; status and priority changes are written to the audit log."""
    task = await service.update(task_id, payload, actor=current_user)
    return TaskRead.model_validate(task, from_attributes=True)


@router.patch(
    "/{task_id}/complete",
    response_model=TaskRead,
    summary="Mark a task as done",
    responses=WRITE_ERRORS,
)
async def complete_task(
    task_id: uuid.UUID,
    service: TaskServiceDep,
    current_user: CurrentUser,
) -> TaskRead:
    """Shortcut for ``PATCH /tasks/{id}`` with ``status=done``."""
    task = await service.complete(task_id, actor=current_user)
    return TaskRead.model_validate(task, from_attributes=True)


@router.delete(
    "/{task_id}",
    response_model=Message,
    summary="Delete a task",
    responses=WRITE_ERRORS,
)
async def delete_task(
    task_id: uuid.UUID,
    service: TaskServiceDep,
    current_user: CurrentUser,
) -> Message:
    """Soft delete, so the activity history stays intact."""
    await service.delete(task_id, actor=current_user)
    return Message(message="Task deleted", detail={"task_id": str(task_id)})


@router.post(
    "/{task_id}/tags",
    response_model=TaskRead,
    summary="Attach a tag to a task",
    responses=WRITE_ERRORS,
)
async def attach_tag(
    task_id: uuid.UUID,
    payload: TagAttach,
    service: TaskServiceDep,
    current_user: CurrentUser,
) -> TaskRead:
    """Idempotent: attaching the same tag twice is a no-op, not an error."""
    task = await service.attach_tag(task_id, tag_id=payload.tag_id, actor=current_user)
    return TaskRead.model_validate(task, from_attributes=True)


@router.delete(
    "/{task_id}/tags/{tag_id}",
    response_model=TaskRead,
    summary="Detach a tag from a task",
    responses=WRITE_ERRORS,
)
async def detach_tag(
    task_id: uuid.UUID,
    tag_id: uuid.UUID,
    service: TaskServiceDep,
    current_user: CurrentUser,
) -> TaskRead:
    """Remove one label; the task itself is untouched."""
    task = await service.detach_tag(task_id, tag_id=tag_id, actor=current_user)
    return TaskRead.model_validate(task, from_attributes=True)


@router.get(
    "/{task_id}/tags",
    response_model=list[TagRead],
    summary="List the tags of a task",
    responses=ACCESS_ERRORS,
)
async def list_task_tags(
    task_id: uuid.UUID,
    service: TaskServiceDep,
    _user: CurrentUser,
) -> list[TagRead]:
    """The labels currently attached to the task."""
    task = await service.get(task_id)
    return [TagRead.model_validate(tag, from_attributes=True) for tag in task.tags]
