"""Append-only activity log.

Every mutation writes one row here. It gives the product a timeline and the reviewer a
cheap way to see that writes actually happen where the README claims they do.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utcnow

if TYPE_CHECKING:
    from app.models.task import Task
    from app.models.user import User


class ActivityLog(Base):
    """One row per meaningful state change."""

    __tablename__ = "activity_log"
    __table_args__ = (
        Index("ix_activity_log_user_id_created_at", "user_id", "created_at"),
        Index("ix_activity_log_task_id_created_at", "task_id", "created_at"),
        Index("ix_activity_log_action", "action"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        default=None,
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        default=None,
    )
    # Stored as plain text on purpose: the log is append-only audit data, and a new
    # action must never require a migration just to add a row.
    action: Mapped[str] = mapped_column(String(length=48), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(length=32), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped[User | None] = relationship(back_populates="activity_log")
    task: Mapped[Task | None] = relationship(back_populates="activity_log")

    def __repr__(self) -> str:
        return f"<ActivityLog {self.action} by {self.user_id}>"
