"""Task — the unit of work, optionally labelled with tags."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import TaskPriority, TaskStatus
from app.db.base import Base, SoftDeleteMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.activity_log import ActivityLog
    from app.models.comment import Comment
    from app.models.project import Project
    from app.models.tag import Tag
    from app.models.user import User


# Association table for the many-to-many between tasks and tags.
task_tags = Table(
    "task_tags",
    Base.metadata,
    Column("task_id", ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)

# Every list endpoint hides soft-deleted rows, so the hot indexes are partial.
_ALIVE = text("deleted_at IS NULL")


class Task(TimestampMixin, SoftDeleteMixin, Base):
    """A single piece of work inside a project."""

    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("position >= 0", name="position_non_negative"),
        CheckConstraint("length(trim(title)) > 0", name="title_not_blank"),
        Index("ix_tasks_project_id_status", "project_id", "status", postgresql_where=_ALIVE),
        Index("ix_tasks_assignee_id_status", "assignee_id", "status", postgresql_where=_ALIVE),
        Index("ix_tasks_due_date", "due_date", postgresql_where=_ALIVE),
        Index("ix_tasks_created_at", "created_at"),
        # Trigram index backing `?search=` on the task title.
        Index(
            "ix_tasks_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        default=None,
    )

    title: Mapped[str] = mapped_column(String(length=200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, default=None)

    status: Mapped[TaskStatus] = mapped_column(
        SAEnum(
            TaskStatus,
            name="task_status",
            native_enum=False,
            length=20,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        default=TaskStatus.TODO,
        nullable=False,
    )
    priority: Mapped[TaskPriority] = mapped_column(
        SAEnum(
            TaskPriority,
            name="task_priority",
            native_enum=False,
            length=16,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        default=TaskPriority.MEDIUM,
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    estimate_minutes: Mapped[int | None] = mapped_column(Integer, default=None)

    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # Timestamp of the last "this task is late" notification, so the nightly Celery
    # job does not mail the assignee about the same task every evening.
    overdue_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=None,
    )

    # Explicit flag kept separate from the enum: `is_overdue` depends on the current
    # time, `overdue_notified_at` on what we already told the user about.
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    project: Mapped[Project] = relationship(back_populates="tasks")
    assignee: Mapped[User | None] = relationship(back_populates="tasks", lazy="joined")
    comments: Mapped[list[Comment]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Comment.created_at",
    )
    tags: Mapped[list[Tag]] = relationship(
        secondary=task_tags, back_populates="tasks", lazy="selectin"
    )
    activity_log: Mapped[list[ActivityLog]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ActivityLog.created_at",
    )

    # ------------------------------------------------------------------ helpers
    @property
    def is_overdue(self) -> bool:
        """Past its due date and not finished yet."""
        if self.due_date is None or self.completed_at is not None:
            return False
        return self.due_date < datetime.now(UTC)

    @property
    def is_done(self) -> bool:
        return self.status is TaskStatus.DONE

    def __repr__(self) -> str:
        return f"<Task {self.title!r} {self.status.value}>"
