"""Project queries."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.project import Project
from app.models.task import Task
from app.repositories.base import BaseRepository


class ProjectRepository(BaseRepository[Project]):
    """Access to the `projects` table."""

    model = Project

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_alive(self, project_id: uuid.UUID) -> Project | None:
        """Fetch a project that has not been soft-deleted."""
        return await self.find_one_by(id=project_id, deleted_at=None)

    def make_listing(
        self,
        *,
        owner_id: uuid.UUID | None = None,
        search: str | None = None,
        is_active: bool | None = None,
    ) -> Select[Any]:
        """Build the filtered `SELECT` used by `GET /projects`.

        `owner_id=None` means "every project" — callers pass the filter only when the
        caller is limited to their own data.
        """
        stmt = select(Project).where(Project.deleted_at.is_(None))
        if owner_id is not None:
            stmt = stmt.where(Project.owner_id == owner_id)
        if is_active is not None:
            stmt = stmt.where(Project.is_active.is_(is_active))
        if search:
            pattern = f"%{search.strip()}%"
            stmt = stmt.where(or_(Project.name.ilike(pattern), Project.description.ilike(pattern)))
        return stmt.order_by(Project.created_at.desc())

    async def recalculate_counters(self, project_id: uuid.UUID) -> None:
        """Recompute `tasks_total` / `tasks_done` from the actual task rows.

        Denormalised counters make the list endpoint cheap; this method is the single
        place allowed to write them, so they can never drift out of sync.
        """
        stmt = select(
            func.count(Task.id).label("total"),
            func.count(Task.id).filter(Task.status == "done").label("done"),
        ).where(Task.project_id == project_id, Task.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        total, done = result.one()

        project = await self.get(project_id)
        if project is not None:
            project.tasks_total = int(total or 0)
            project.tasks_done = int(done or 0)
            await self.session.flush()

    async def count_owned(self, owner_id: uuid.UUID) -> int:
        """How many live projects a user owns."""
        return await self.count(owner_id=owner_id, deleted_at=None)

    async def count_by_activity(self, owner_id: uuid.UUID) -> dict[str, int]:
        """Projects per activity flag, for the dashboard."""
        stmt = select(Project.is_active, func.count()).where(
            Project.owner_id == owner_id,
            Project.deleted_at.is_(None),
        )
        result = await self.session.execute(stmt)
        counters: dict[str, int] = {"active": 0, "inactive": 0}
        for is_active, count in result.all():
            counters["active" if is_active else "inactive"] = int(count)
        return counters

    def make_with_owner(self) -> Select[Any]:
        """`SELECT` that eagerly loads the owner — avoids an N+1 on name rendering."""
        return select(Project).options(selectinload(Project.owner))
