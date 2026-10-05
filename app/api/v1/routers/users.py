"""User administration endpoints (staff only).

Uses the shared pieces: `user_filters()` for the query string, `build_page()` for the
envelope, `ACCESS_ERRORS`/`WRITE_ERRORS` for the OpenAPI error blocks and
`app.core.permissions` for every access question.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, PaginationDep, RequireAdmin, RequireStaff, UserServiceDep
from app.api.filters import FilterSet, user_filters
from app.api.responses import ACCESS_ERRORS, WRITE_ERRORS, build_page
from app.core.constants import Role
from app.core.errors import PermissionDeniedError
from app.core.permissions import can_manage_user
from app.schemas.common import Message, PaginatedResponse
from app.schemas.user import UserCreate, UserRead, UserUpdate

router = APIRouter()

UserFilters = Annotated[FilterSet, Depends(user_filters)]


@router.get(
    "",
    response_model=PaginatedResponse[UserRead],
    summary="List users (staff only)",
    responses=ACCESS_ERRORS,
)
async def list_users(
    service: UserServiceDep,
    _staff: RequireStaff,
    pagination: PaginationDep,
    filters: UserFilters,
) -> PaginatedResponse[UserRead]:
    """Paginated account directory with filtering."""
    items, total = await service.list(**pagination.as_kwargs(), **filters.to_repository())
    return build_page(items, total, pagination, UserRead)


@router.post(
    "",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account (admin only)",
    responses=WRITE_ERRORS,
)
async def create_user(
    payload: UserCreate,
    service: UserServiceDep,
    actor: RequireAdmin,
    role: Annotated[Role, Query(description="Role for the new account")] = Role.USER,
) -> UserRead:
    """Create an account with an explicit role.

    Self-registration cannot do this: it always creates a plain ``user``.
    """
    user = await service.create(payload, role=role, actor=actor)
    return UserRead.model_validate(user, from_attributes=True)


@router.get(
    "/{user_id}",
    response_model=UserRead,
    summary="Read a user profile",
    responses=ACCESS_ERRORS,
)
async def read_user(
    user_id: uuid.UUID,
    service: UserServiceDep,
    current_user: CurrentUser,
) -> UserRead:
    """Any authenticated user may read their own profile; staff may read anyone's."""
    target = await service.get(user_id)
    if not can_manage_user(current_user, target):
        raise PermissionDeniedError("You may only read your own profile")
    return UserRead.model_validate(target, from_attributes=True)


@router.patch(
    "/{user_id}",
    response_model=UserRead,
    summary="Update a user profile",
    responses=WRITE_ERRORS,
)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    service: UserServiceDep,
    actor: CurrentUser,
) -> UserRead:
    """Self-service for your own account; admins may edit anyone.

    Only an admin may change ``is_active`` or ``role`` — see `UserService._clean_values`.
    """
    user = await service.update(user_id, payload, actor=actor)
    return UserRead.model_validate(user, from_attributes=True)


@router.delete(
    "/{user_id}",
    response_model=Message,
    summary="Soft-delete a user (admin only)",
    responses=WRITE_ERRORS,
)
async def delete_user(user_id: uuid.UUID, service: UserServiceDep, actor: RequireAdmin) -> Message:
    """The row survives for audit, but the account can no longer log in."""
    await service.deactivate(user_id, actor=actor)
    return Message(message="User deactivated", detail={"user_id": str(user_id)})


@router.post(
    "/{user_id}/activate",
    response_model=UserRead,
    summary="Restore a soft-deleted user (admin only)",
    responses=WRITE_ERRORS,
)
async def activate_user(
    user_id: uuid.UUID, service: UserServiceDep, actor: RequireAdmin
) -> UserRead:
    """Reactivate a previously soft-deleted account."""
    user = await service.reactivate(user_id, actor=actor)
    return UserRead.model_validate(user, from_attributes=True)
