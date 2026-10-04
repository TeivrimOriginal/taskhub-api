"""User administration endpoints (staff only)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, PaginationDep, RequireAdmin, RequireStaff, UserServiceDep
from app.core.constants import Role
from app.core.errors import PermissionDeniedError
from app.models.user import User
from app.schemas.common import ErrorResponse, Message, PaginatedResponse
from app.schemas.user import UserCreate, UserRead, UserUpdate

router = APIRouter()

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse, "description": "Not authenticated"},
    403: {"model": ErrorResponse, "description": "Insufficient role"},
    404: {"model": ErrorResponse, "description": "User not found"},
}


@router.get(
    "",
    response_model=PaginatedResponse[UserRead],
    summary="List users (staff only)",
    responses=ERROR_RESPONSES,
)
async def list_users(
    service: UserServiceDep,
    _staff: RequireStaff,
    pagination: PaginationDep,
    search: Annotated[
        str | None, Query(max_length=120, description="Match e-mail, username or name")
    ] = None,
    role: Annotated[Role | None, Query(description="Filter by role")] = None,
    is_active: Annotated[bool | None, Query(description="Filter by activation flag")] = None,
    include_deleted: Annotated[bool, Query(description="Include soft-deleted accounts")] = False,
) -> PaginatedResponse[UserRead]:
    """Paginated account directory with filtering."""
    items, total = await service.list(
        page=pagination.page,
        size=pagination.size,
        search=search,
        role=role,
        is_active=is_active,
        include_deleted=include_deleted,
    )
    return PaginatedResponse[UserRead].create(
        [UserRead.model_validate(user, from_attributes=True) for user in items],
        total=total,
        page=pagination.page,
        size=pagination.size,
    )


@router.post(
    "",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account (admin only)",
    responses=ERROR_RESPONSES,
)
async def create_user(
    payload: UserCreate,
    service: UserServiceDep,
    actor: RequireAdmin,
    role: Annotated[Role, Query(description="Role for the new account")] = Role.USER,
) -> UserRead:
    """Create an account with an explicit role (self-registration cannot do this)."""
    user = await service.create(payload, role=role, actor=actor)
    return UserRead.model_validate(user, from_attributes=True)


@router.get(
    "/{user_id}",
    response_model=UserRead,
    summary="Read a user profile",
    responses=ERROR_RESPONSES,
)
async def read_user(
    user_id: uuid.UUID,
    service: UserServiceDep,
    current_user: CurrentUser,
) -> UserRead:
    """Any authenticated user may read their own profile; staff may read anyone's."""
    target: User = await service.get(user_id)
    if current_user.id != target.id and not current_user.is_staff:
        raise PermissionDeniedError("You may only read your own profile")
    return UserRead.model_validate(target, from_attributes=True)


@router.patch(
    "/{user_id}",
    response_model=UserRead,
    summary="Update a user profile",
    responses=ERROR_RESPONSES,
)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    service: UserServiceDep,
    actor: CurrentUser,
) -> UserRead:
    """Self-service for your own account; admins may edit anyone."""
    user = await service.update(user_id, payload, actor=actor)
    return UserRead.model_validate(user, from_attributes=True)


@router.delete(
    "/{user_id}",
    response_model=Message,
    summary="Soft-delete a user (admin only)",
    responses=ERROR_RESPONSES,
)
async def delete_user(user_id: uuid.UUID, service: UserServiceDep, actor: RequireAdmin) -> Message:
    """Soft delete: the row survives for audit, but the account can no longer log in."""
    await service.deactivate(user_id, actor=actor)
    return Message(message="User deactivated", detail={"user_id": str(user_id)})


@router.post(
    "/{user_id}/activate",
    response_model=UserRead,
    summary="Restore a soft-deleted user (admin only)",
    responses=ERROR_RESPONSES,
)
async def activate_user(
    user_id: uuid.UUID, service: UserServiceDep, actor: RequireAdmin
) -> UserRead:
    """Reactivate a previously soft-deleted account."""
    user = await service.reactivate(user_id, actor=actor)
    return UserRead.model_validate(user, from_attributes=True)
