"""User account."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, Index, String
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import STAFF_ROLES, Role
from app.db.base import Base, SoftDeleteMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.activity_log import ActivityLog
    from app.models.comment import Comment
    from app.models.project import Project
    from app.models.refresh_token import RefreshToken
    from app.models.task import Task


class User(TimestampMixin, SoftDeleteMixin, Base):
    """A registered account.

    `email` and `username` are `citext`, so uniqueness is case-insensitive without
    relying on a functional index that a future migration could get wrong.
    """

    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_role_is_active", "role", "is_active"),
        Index("ix_users_created_at", "created_at"),
        {"comment": "Application accounts; password hash never leaves the server."},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(CITEXT(length=320), unique=True, nullable=False)
    username: Mapped[str] = mapped_column(CITEXT(length=64), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(length=128), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(length=200), default=None)
    role: Mapped[Role] = mapped_column(
        SAEnum(
            Role,
            name="user_role",
            native_enum=False,
            length=16,
            # Store the enum *values* ("user", not "USER") so the database, the API and
            # the docs all speak the same language.
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        default=Role.USER,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # ------------------------------------------------------------ relationships
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    projects: Mapped[list[Project]] = relationship(
        back_populates="owner",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    tasks: Mapped[list[Task]] = relationship(
        back_populates="assignee",
        passive_deletes=True,
    )
    comments: Mapped[list[Comment]] = relationship(
        back_populates="author",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    activity_log: Mapped[list[ActivityLog]] = relationship(
        back_populates="user",
        passive_deletes=True,
    )

    # ------------------------------------------------------------------ helpers
    @property
    def is_staff(self) -> bool:
        """True for staff: a moderator, an admin, or a superuser.

        This is the *definition*. Everything else asks `app.core.permissions.is_staff()`,
        which simply calls this — so the rule about what counts as staff lives here and
        only here, and the rule about what staff may *do* lives in `permissions`.
        """
        return self.role in STAFF_ROLES or self.is_superuser

    def __repr__(self) -> str:
        return f"<User {self.username} ({self.role.value})>"
