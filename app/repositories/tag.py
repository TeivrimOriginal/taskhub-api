"""Tag queries."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag import Tag
from app.models.task import Task, task_tags
from app.repositories.base import BaseRepository


class TagRepository(BaseRepository[Tag]):
    """Access to the `tags` table."""

    model = Tag

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    def make_listing(self, *, search: str | None = None) -> Select[Any]:
        """Alphabetical tag catalogue, optionally filtered by name."""
        stmt = select(Tag)
        if search:
            stmt = stmt.where(Tag.name.ilike(f"{search.strip()}%"))
        return stmt.order_by(Tag.name.asc())

    async def usage_count(self, tag_id: uuid.UUID) -> int:
        """How many live tasks carry this tag."""
        result = await self.session.execute(
            select(func.count())
            .select_from(task_tags.join(Task, Task.id == task_tags.c.task_id))
            .where(task_tags.c.tag_id == tag_id, Task.deleted_at.is_(None))
        )
        return int(result.scalar_one())

    async def most_used(self, *, limit: int = 10) -> list[tuple[Tag, int]]:
        """Tags ordered by how many live tasks use them."""
        stmt = (
            select(Tag, func.count(task_tags.c.task_id).label("usage"))
            .join(task_tags, task_tags.c.tag_id == Tag.id)
            .join(Task, Task.id == task_tags.c.task_id)
            .where(Task.deleted_at.is_(None))
            .group_by(Tag.id)
            .order_by(func.count(task_tags.c.task_id).desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return [(row[0], int(row[1])) for row in result.all()]

    async def get_many(self, tag_ids: list[uuid.UUID]) -> list[Tag]:
        """Fetch several tags at once, preserving nothing — used for validation."""
        if not tag_ids:
            return []
        stmt = select(Tag).where(Tag.id.in_(tag_ids))
        return await self._scalars(stmt)
