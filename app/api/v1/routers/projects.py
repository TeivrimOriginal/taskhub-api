"""Project endpoints.

Everything that repeats across the API lives elsewhere on purpose: the error blocks come
from `app.api.responses`, the pagination envelope from `build_page`, the filters from
`app.api.filters` and the access rules from `app.core.permissions`. A handler here only
describes what this endpoint does.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import CurrentUser, PaginationDep, ProjectServiceDep, TaskServiceDep
from app.api.filters import FilterSet, project_filters, task_filters
from app.api.responses import ACCESS_ERRORS, WRITE_ERRORS, build_page, to_schema
from app.core.permissions import assert_read_project
from app.schemas.common import Message, PaginatedResponse
from app.schemas.project import ProjectCreate, ProjectRead, ProjectUpdate
from app.schemas.task import TaskRead

router = APIRouter()

#: The filters of this router, resolved by FastAPI into query parameters.
ProjectFilters = Annotated[FilterSet, Depends(project_filters)]
TaskFilters = Annotated[FilterSet, Depends(task_filters)]


@router.post(
    "",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project",
    responses=WRITE_ERRORS,
)
async def create_project(
    payload: ProjectCreate,
    service: ProjectServiceDep,
    current_user: CurrentUser,
) -> ProjectRead:
    """Create a project.

    The owner is always the caller: the field is not accepted from the body, so a client
    cannot create a project "for" somebody else.
    """
    project = await service.create(payload, owner=current_user)
    return to_schema(project, ProjectRead)


@router.get(
    "",
    response_model=PaginatedResponse[ProjectRead],
    summary="List projects",
    responses=ACCESS_ERRORS,
)
async def list_projects(
    service: ProjectServiceDep,
    current_user: CurrentUser,
    pagination: PaginationDep,
    filters: ProjectFilters,
) -> PaginatedResponse[ProjectRead]:
    """Your own projects; staff see every project.

    The filters are declared in `app.api.filters`, so this handler never names them.
    """
    items, total = await service.list_for_user(
        user=current_user,
        **pagination.as_kwargs(),
        **filters.to_repository(),
    )
    return build_page(items, total, pagination, ProjectRead)


@router.get(
    "/{project_id}",
    response_model=ProjectRead,
    summary="Read a project",
    responses=ACCESS_ERRORS,
)
async def read_project(
    project_id: uuid.UUID,
    service: ProjectServiceDep,
    current_user: CurrentUser,
) -> ProjectRead:
    """Project detail, including the denormalised task counters."""
    project = await service.get(project_id)
    assert_read_project(current_user, project)
    return to_schema(project, ProjectRead)


@router.patch(
    "/{project_id}",
    response_model=ProjectRead,
    summary="Update a project",
    responses=WRITE_ERRORS,
)
async def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    service: ProjectServiceDep,
    current_user: CurrentUser,
) -> ProjectRead:
    """Partial update. Only the owner or an admin may do it."""
    project = await service.update(project_id, payload, actor=current_user)
    return to_schema(project, ProjectRead)


@router.delete(
    "/{project_id}",
    response_model=Message,
    summary="Delete a project and its tasks",
    responses=WRITE_ERRORS,
)
async def delete_project(
    project_id: uuid.UUID,
    service: ProjectServiceDep,
    current_user: CurrentUser,
) -> Message:
    """Soft delete: the project and its tasks disappear, the audit history stays."""
    await service.delete(project_id, actor=current_user)
    return Message(message="Project deleted", detail={"project_id": str(project_id)})


@router.get(
    "/{project_id}/tasks",
    response_model=PaginatedResponse[TaskRead],
    summary="List the tasks of a project",
    responses=ACCESS_ERRORS,
)
async def list_project_tasks(
    project_id: uuid.UUID,
    project_service: ProjectServiceDep,
    task_service: TaskServiceDep,
    current_user: CurrentUser,
    pagination: PaginationDep,
    filters: TaskFilters,
) -> PaginatedResponse[TaskRead]:
    """Tasks of one project, with the same envelope as every other list endpoint.

    The same task filters as ``GET /tasks`` are available, except ``project_id``: the path
    already decides it, so the service overwrites whatever the query string asked for.
    """
    project = await project_service.get(project_id)
    assert_read_project(current_user, project)

    items, total = await task_service.list_for_project(
        project=project,
        **pagination.as_kwargs(),
        **filters.to_repository(),
    )
    return build_page(items, total, pagination, TaskRead)
