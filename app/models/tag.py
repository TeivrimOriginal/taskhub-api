"""Tag — reusable label shared across tasks."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.task import Task


class Tag(TimestampMixin, Base):
    """A label such as `backend` or `urgent`.

    `name` is stored already lower-cased by `TagService` (see `normalise_tag_name`), so
    the unique constraint gives case-insensitive uniqueness without a functional index.
    """

    __tablename__ = "tags"
    __table_args__ = (CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(length=50), unique=True, nullable=False)
    color: Mapped[str] = mapped_column(String(length=7), default="#0EA5E9", nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        default=None,
    )

    tasks: Mapped[list[Task]] = relationship(
        secondary="task_tags",
        back_populates="tags",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Tag {self.name!r}>"
