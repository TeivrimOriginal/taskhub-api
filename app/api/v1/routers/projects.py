"""Project endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, PaginationDep, ProjectServiceDep, TaskServiceDep
from app.core.constants import TaskStatus
from app.schemas.common import ErrorResponse, Message, PaginatedResponse
from app.schemas.project import ProjectCreate, ProjectRead, ProjectUpdate
from app.schemas.task import TaskRead

router = APIRouter()

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse, "description": "Not authenticated"},
    403: {"model": ErrorResponse, "description": "Not the owner and not an admin"},
    404: {"model": ErrorResponse, "description": "Project not found"},
}


@router.post(
    "",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project",
    responses=ERROR_RESPONSES,
)
async def create_project(
    payload: ProjectCreate,
    service: ProjectServiceDep,
    current_user: CurrentUser,
) -> ProjectRead:
    """The owner is always the caller — the field is not accepted from the body."""
    project = await service.create(payload, owner=current_user)
    return ProjectRead.model_validate(project, from_attributes=True)


@router.get(
    "",
    response_model=PaginatedResponse[ProjectRead],
    summary="List projects",
    responses=ERROR_RESPONSES,
)
async def list_projects(
    service: ProjectServiceDep,
    current_user: CurrentUser,
    pagination: PaginationDep,
    search: Annotated[
        str | None, Query(max_length=120, description="Match name or description")
    ] = None,
    is_active: Annotated[bool | None, Query(description="Filter by active flag")] = None,
) -> PaginatedResponse[ProjectRead]:
    """Your own projects; staff see every project."""
    items, total = await service.list_for_user(
        user=current_user,
        page=pagination.page,
        size=pagination.size,
        search=search,
        is_active=is_active,
    )
    return PaginatedResponse[ProjectRead].create(
        [ProjectRead.model_validate(project, from_attributes=True) for project in items],
        total=total,
        page=pagination.page,
        size=pagination.size,
    )


@router.get(
    "/{project_id}",
    response_model=ProjectRead,
    summary="Read a project",
    responses=ERROR_RESPONSES,
)
async def read_project(
    project_id: uuid.UUID,
    service: ProjectServiceDep,
    _current_user: CurrentUser,
) -> ProjectRead:
    """Project detail including the denormalised task counters."""
    project = await service.get(project_id)
    return ProjectRead.model_validate(project, from_attributes=True)


@router.patch(
    "/{project_id}",
    response_model=ProjectRead,
    summary="Update a project",
    responses=ERROR_RESPONSES,
)
async def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    service: ProjectServiceDep,
    current_user: CurrentUser,
) -> ProjectRead:
    """Owner or admin only."""
    project = await service.update(project_id, payload, actor=current_user)
    return ProjectRead.model_validate(project, from_attributes=True)


@router.delete(
    "/{project_id}",
    response_model=Message,
    summary="Delete a project and its tasks",
    responses=ERROR_RESPONSES,
)
async def delete_project(
    project_id: uuid.UUID,
    service: ProjectServiceDep,
    current_user: CurrentUser,
) -> Message:
    """Soft delete: the project and its tasks disappear but stay in the audit trail."""
    await service.delete(project_id, actor=current_user)
    return Message(message="Project deleted", detail={"project_id": str(project_id)})


@router.get(
    "/{project_id}/tasks",
    response_model=PaginatedResponse[TaskRead],
    summary="List the tasks of a project",
    responses=ERROR_RESPONSES,
)
async def list_project_tasks(
    project_id: uuid.UUID,
    project_service: ProjectServiceDep,
    task_service: TaskServiceDep,
    _current_user: CurrentUser,
    pagination: PaginationDep,
    status_filter: Annotated[
        TaskStatus | None,
        Query(alias="status", description="Filter by task status"),
    ] = None,
    search: Annotated[
        str | None, Query(max_length=200, description="Match title or description")
    ] = None,
) -> PaginatedResponse[TaskRead]:
    """Tasks of one project, with the same envelope as every other list endpoint."""
    project = await project_service.get(project_id)
    items, total = await task_service.list_for_project(
        project=project,
        page=pagination.page,
        size=pagination.size,
        status=status_filter,
        search=search,
    )
    return PaginatedResponse[TaskRead].create(
        [TaskRead.model_validate(task, from_attributes=True) for task in items],
        total=total,
        page=pagination.page,
        size=pagination.size,
    )
