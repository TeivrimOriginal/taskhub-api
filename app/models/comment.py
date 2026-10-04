"""Comment attached to a task."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.task import Task
    from app.models.user import User


class Comment(TimestampMixin, SoftDeleteMixin, Base):
    """A threaded-free, flat note on a task."""

    __tablename__ = "comments"
    __table_args__ = (
        CheckConstraint("length(trim(body)) > 0", name="body_not_blank"),
        Index("ix_comments_task_id_created_at", "task_id", "created_at"),
        # Trigram index: comments are the one free-text column that gets searched.
        Index(
            "ix_comments_body_trgm",
            "body",
            postgresql_using="gin",
            postgresql_ops={"body": "gin_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)

    task: Mapped[Task] = relationship(back_populates="comments")
    author: Mapped[User] = relationship(back_populates="comments", lazy="joined")

    def __repr__(self) -> str:
        return f"<Comment by {self.author_id}>"
