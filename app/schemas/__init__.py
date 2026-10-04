"""Pydantic schemas — the only shapes that cross the HTTP boundary."""

from app.schemas.auth import (
    LoginRequest,
    RefreshTokenRequest,
    TokenPair,
)
from app.schemas.comment import CommentCreate, CommentRead, CommentUpdate
from app.schemas.common import (
    ErrorBody,
    ErrorResponse,
    Message,
    PaginatedResponse,
    PaginationMeta,
)
from app.schemas.project import (
    ProjectCreate,
    ProjectRead,
    ProjectSummary,
    ProjectUpdate,
)
from app.schemas.stats import (
    PriorityBreakdown,
    StatsOverview,
    StatusBreakdown,
)
from app.schemas.tag import TagCreate, TagRead, TagUpdate
from app.schemas.task import (
    TaskCreate,
    TaskRead,
    TaskUpdate,
)
from app.schemas.user import (
    PasswordChange,
    UserCreate,
    UserRead,
    UserSummary,
    UserUpdate,
)

__all__ = [
    "CommentCreate",
    "CommentRead",
    "CommentUpdate",
    "ErrorBody",
    "ErrorResponse",
    "LoginRequest",
    "Message",
    "PaginatedResponse",
    "PaginationMeta",
    "PasswordChange",
    "PriorityBreakdown",
    "ProjectCreate",
    "ProjectRead",
    "ProjectSummary",
    "ProjectUpdate",
    "RefreshTokenRequest",
    "StatsOverview",
    "StatusBreakdown",
    "TagAttach",
    "TagCreate",
    "TagRead",
    "TagUpdate",
    "TaskCreate",
    "TaskRead",
    "TaskUpdate",
    "TokenPair",
    "UserCreate",
    "UserRead",
    "UserSummary",
    "UserUpdate",
]
