"""Project — a container for tasks."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.task import Task
    from app.models.user import User


class Project(TimestampMixin, SoftDeleteMixin, Base):
    """A workspace that owns tasks and has exactly one owner."""

    __tablename__ = "projects"
    __table_args__ = (
        Index("ix_projects_owner_id_is_active", "owner_id", "is_active"),
        # Trigram index for `?search=` (ILIKE '%term%'); without it the search degrades
        # into a sequential scan.
        Index(
            "ix_projects_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(length=120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    color: Mapped[str] = mapped_column(String(length=7), default="#4F46E5", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Denormalised counters: kept up to date by `ProjectService` so list endpoints can
    # render a dashboard without a correlated subquery per row.
    tasks_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    tasks_done: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")

    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    owner: Mapped[User] = relationship(back_populates="projects")
    tasks: Mapped[list[Task]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Task.position",
    )

    @property
    def completion_rate(self) -> float:
        """Share of finished tasks, 0.0 for an empty project."""
        if not self.tasks_total:
            return 0.0
        return round(self.tasks_done / self.tasks_total, 4)

    def __repr__(self) -> str:
        return f"<Project {self.name!r}>"
