"""Task queries — the most filter-heavy repository in the project."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.constants import TaskPriority, TaskStatus
from app.models.project import Project
from app.models.tag import Tag
from app.models.task import Task, task_tags
from app.repositories.base import BaseRepository

_ALIVE = Task.deleted_at.is_(None)
_OPEN_STATUSES = (TaskStatus.TODO, TaskStatus.IN_PROGRESS, TaskStatus.IN_REVIEW)


class TaskRepository(BaseRepository[Task]):
    """Access to the `tasks` table."""

    model = Task

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_alive(self, task_id: uuid.UUID) -> Task | None:
        """Fetch a task with everything needed to render `TaskRead`."""
        stmt = (
            select(Task)
            .options(
                selectinload(Task.tags),
                selectinload(Task.project),
                selectinload(Task.assignee),
            )
            .where(Task.id == task_id, _ALIVE)
        )
        return await self._scalar(stmt)

    def make_listing(
        self,
        *,
        project_id: uuid.UUID | None = None,
        owner_id: uuid.UUID | None = None,
        assignee_id: uuid.UUID | None = None,
        status: TaskStatus | None = None,
        priority: TaskPriority | None = None,
        search: str | None = None,
        tag_ids: list[uuid.UUID] | None = None,
        overdue_only: bool = False,
        due_before: datetime | None = None,
        created_from: datetime | None = None,
    ) -> Select[Any]:
        """Build the filtered `SELECT` behind every task listing.

        Each filter maps one-to-one onto an index declared in the model
        (`ix_tasks_project_id_status`, `ix_tasks_assignee_id_status`,
        `ix_tasks_due_date`), which is why they are separate keyword arguments rather
        than a free-form filter object.
        """
        stmt = (
            select(Task).options(selectinload(Task.tags), selectinload(Task.project)).where(_ALIVE)
        )

        if project_id is not None:
            stmt = stmt.where(Task.project_id == project_id)
        if owner_id is not None:
            # Filter by project owner, which needs an EXISTS against `projects`.
            owned = select(Project.id).where(
                Project.owner_id == owner_id,
                Project.deleted_at.is_(None),
            )
            stmt = stmt.where(Task.project_id.in_(owned))
        if assignee_id is not None:
            stmt = stmt.where(Task.assignee_id == assignee_id)
        if status is not None:
            stmt = stmt.where(Task.status == status)
        if priority is not None:
            stmt = stmt.where(Task.priority == priority)
        if search:
            pattern = f"%{search.strip()}%"
            stmt = stmt.where(or_(Task.title.ilike(pattern), Task.description.ilike(pattern)))
        if tag_ids:
            # One EXISTS per tag means "carries every requested tag", which a single
            # `IN (...)` on the association table cannot express.
            for tag_id in tag_ids:
                stmt = stmt.where(
                    exists().where(
                        (task_tags.c.task_id == Task.id) & (task_tags.c.tag_id == tag_id)
                    )
                )
        if overdue_only or due_before is not None:
            stmt = stmt.where(
                Task.due_date.is_not(None),
                Task.completed_at.is_(None),
                Task.status.in_(_OPEN_STATUSES),
            )
            if due_before is not None:
                stmt = stmt.where(Task.due_date <= due_before)
            elif overdue_only:
                stmt = stmt.where(Task.due_date < datetime.now(UTC))
        if created_from is not None:
            stmt = stmt.where(Task.created_at >= created_from)

        return stmt.order_by(Task.created_at.desc())

    async def count_for_project(
        self,
        project_id: uuid.UUID,
        status: TaskStatus | None = None,
    ) -> int:
        """Tasks in a project, optionally of one status."""
        stmt = select(Task.id).where(Task.project_id == project_id, _ALIVE)
        if status is not None:
            stmt = stmt.where(Task.status == status)
        result = await self.session.execute(select(func.count()).select_from(stmt.subquery()))
        return int(result.scalar_one())

    async def next_position(self, project_id: uuid.UUID) -> int:
        """Next free ordering slot inside a project."""
        result = await self.session.execute(
            select(func.coalesce(func.max(Task.position), -1) + 1).where(
                Task.project_id == project_id,
                _ALIVE,
            )
        )
        return int(result.scalar_one())

    async def bulk_overdue(self, *, limit: int = 500) -> list[Task]:
        """Tasks past their due date that have not been announced yet."""
        stmt = (
            select(Task)
            .options(selectinload(Task.assignee))
            .where(
                _ALIVE,
                Task.due_date.is_not(None),
                Task.due_date < datetime.now(UTC),
                Task.completed_at.is_(None),
                Task.status.in_(_OPEN_STATUSES),
                Task.overdue_notified_at.is_(None),
            )
            .limit(limit)
        )
        return await self._scalars(stmt)

    async def attach_tag(self, task: Task, tag: Tag) -> bool:
        """Attach a tag; `False` when it was already attached (idempotent)."""
        if any(existing.id == tag.id for existing in task.tags):
            return False
        task.tags.append(tag)
        await self.session.flush()
        return True

    async def detach_tag(self, task: Task, tag_id: uuid.UUID) -> bool:
        """Remove a tag from a task; `False` when it was not attached."""
        for existing in list(task.tags):
            if existing.id == tag_id:
                task.tags.remove(existing)
                await self.session.flush()
                return True
        return False
