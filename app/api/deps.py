"""FastAPI dependencies — the only place that knows about `Request`, sessions and roles.

Route handlers stay declarative: they receive ready objects and never build queries.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.constants import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, STAFF_ROLES, Role
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.db.session import get_session
from app.models.user import User
from app.services.auth import AuthService
from app.services.comment import CommentService
from app.services.project import ProjectService
from app.services.stats import StatsService
from app.services.tag import TagService
from app.services.task import TaskService
from app.services.user import UserService

# `auto_error=False` so a missing header raises our own AuthenticationError and produces
# the standard error envelope instead of FastAPI's bare {"detail": ...}.
bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


# --------------------------------------------------------------- pagination
class PaginationParams:
    """Reusable `?page=&size=` dependency with hard caps.

    Clamping (instead of rejecting) keeps a client that asks for 1000 items working
    while still protecting the database.
    """

    def __init__(
        self,
        page: Annotated[int, Query(ge=1, le=10_000, description="1-based page")] = 1,
        size: Annotated[
            int,
            Query(ge=1, le=MAX_PAGE_SIZE, description=f"Items per page (max {MAX_PAGE_SIZE})"),
        ] = DEFAULT_PAGE_SIZE,
    ) -> None:
        self.page = page
        self.size = size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size

    def as_kwargs(self) -> dict[str, int]:
        return {"page": self.page, "size": self.size}


PaginationDep = Annotated[PaginationParams, Depends(PaginationParams)]


# -------------------------------------------------------------------- auth
def get_auth_service(session: SessionDep, settings: SettingsDep) -> AuthService:
    """Build the auth service for the request."""
    return AuthService(session, settings)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


async def get_current_user(
    request: Request,
    auth: AuthServiceDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    """Resolve the bearer token into a live, active user.

    The session is not requested here: `AuthService` already owns one for the request, and
    a second `Depends(get_session)` would create a second transaction.
    """
    token = _extract_token(request, credentials)
    user = await auth.authenticate(access_token=token)
    request.state.user_id = str(user.id)
    return user


async def get_current_active_user(user: Annotated[User, Depends(get_current_user)]) -> User:
    """The authenticated user, guaranteed active."""
    if not user.is_active:
        raise AuthenticationError("This account is deactivated")
    return user


CurrentUser = Annotated[User, Depends(get_current_active_user)]
OptionalUser = Annotated[User | None, Depends(get_current_user)]


def require_roles(*roles: Role) -> Callable[[User], Awaitable[User]]:
    """Build a dependency that admits only the listed roles.

    A dependency factory instead of an `if` inside every handler: the permission rule is
    declared once, next to the URL that needs it, and cannot be forgotten halfway.
    """

    async def _dependency(user: CurrentUser) -> User:
        if user.role not in roles:
            raise PermissionDeniedError(
                "This action requires one of: " + ", ".join(role.value for role in roles),
                details={"required_roles": [role.value for role in roles]},
            )
        return user

    return _dependency


RequireStaff = Annotated[User, Depends(require_roles(*STAFF_ROLES))]
RequireAdmin = Annotated[User, Depends(require_roles(Role.ADMIN))]


def _extract_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
) -> str:
    """Accept the token from `Authorization: Bearer` (or the Swagger auth header)."""
    if credentials is not None and credentials.scheme.lower() == "bearer":
        return credentials.credentials
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    raise AuthenticationError("Authorization header with a bearer token is required")


# ----------------------------------------------------------------- services
def get_user_service(session: SessionDep) -> UserService:
    return UserService(session)


def get_project_service(session: SessionDep) -> ProjectService:
    return ProjectService(session)


def get_task_service(session: SessionDep) -> TaskService:
    return TaskService(session)


def get_comment_service(session: SessionDep) -> CommentService:
    return CommentService(session)


def get_tag_service(session: SessionDep) -> TagService:
    return TagService(session)


def get_stats_service(session: SessionDep, settings: SettingsDep) -> StatsService:
    return StatsService(session, settings)


UserServiceDep = Annotated[UserService, Depends(get_user_service)]
ProjectServiceDep = Annotated[ProjectService, Depends(get_project_service)]
TaskServiceDep = Annotated[TaskService, Depends(get_task_service)]
CommentServiceDep = Annotated[CommentService, Depends(get_comment_service)]
TagServiceDep = Annotated[TagService, Depends(get_tag_service)]
StatsServiceDep = Annotated[StatsService, Depends(get_stats_service)]
