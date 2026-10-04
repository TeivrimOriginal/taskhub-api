"""Comment queries."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.comment import Comment
from app.repositories.base import BaseRepository

_ALIVE = Comment.deleted_at.is_(None)


class CommentRepository(BaseRepository[Comment]):
    """Access to the `comments` table."""

    model = Comment

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_alive(self, comment_id: uuid.UUID) -> Comment | None:
        """Fetch a comment (with its author) that has not been soft-deleted."""
        stmt = (
            select(Comment)
            .options(selectinload(Comment.author))
            .where(Comment.id == comment_id, _ALIVE)
        )
        return await self._scalar(stmt)

    def make_listing(
        self,
        *,
        task_id: uuid.UUID | None = None,
        author_id: uuid.UUID | None = None,
    ) -> Select[Any]:
        """Newest-first comments, optionally scoped to a task or an author."""
        stmt = select(Comment).options(selectinload(Comment.author)).where(_ALIVE)
        if task_id is not None:
            stmt = stmt.where(Comment.task_id == task_id)
        if author_id is not None:
            stmt = stmt.where(Comment.author_id == author_id)
        return stmt.order_by(Comment.created_at.desc())

    async def count_for_task(self, task_id: uuid.UUID) -> int:
        """Number of live comments on a task — cheap enough for the task detail view."""
        result = await self.session.execute(
            select(func.count()).select_from(Comment).where(Comment.task_id == task_id, _ALIVE)
        )
        return int(result.scalar_one())
