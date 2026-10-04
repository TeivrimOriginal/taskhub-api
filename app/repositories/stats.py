"""Aggregate queries for the dashboard.

Every statistic is computed by the database with a single grouped query instead of by
loading rows into Python — the difference between one round trip and thousands.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import Subquery

from app.core.constants import TaskPriority, TaskStatus
from app.models.comment import Comment
from app.models.project import Project
from app.models.task import Task

_OPEN_STATUSES = (TaskStatus.TODO, TaskStatus.IN_PROGRESS, TaskStatus.IN_REVIEW)


class StatsRepository:
    """Read-only aggregation queries (no generic CRUD needed here)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _owned_tasks(self, user_id: uuid.UUID) -> Subquery:
        """Live tasks inside projects owned by `user_id`, as a queryable alias.

        An alias (not a plain subquery) so the aggregate queries can filter and group on
        its columns while keeping one definition of "the user's tasks".
        """
        owned_projects = select(Project.id).where(
            Project.owner_id == user_id,
            Project.deleted_at.is_(None),
        )
        stmt = select(
            Task.id,
            Task.status,
            Task.priority,
            Task.due_date,
            Task.completed_at,
            Task.created_at,
            Task.assignee_id,
        ).where(Task.project_id.in_(owned_projects), Task.deleted_at.is_(None))
        return stmt.subquery("owned_tasks")

    def _my_task_ids(self, user_id: uuid.UUID) -> Select[Any]:
        """Ids of live tasks inside the user's projects (used for comment counts)."""
        return (
            select(Task.id)
            .join(Project, Project.id == Task.project_id)
            .where(
                Project.owner_id == user_id,
                Project.deleted_at.is_(None),
                Task.deleted_at.is_(None),
            )
        )

    async def count_projects(self, user_id: uuid.UUID) -> int:
        """Live projects owned by the user."""
        result = await self.session.execute(
            select(func.count())
            .select_from(Project)
            .where(Project.owner_id == user_id, Project.deleted_at.is_(None))
        )
        return int(result.scalar_one())

    async def count_tasks(self, user_id: uuid.UUID) -> int:
        """Live tasks inside the user's projects."""
        owned = self._owned_tasks(user_id)
        result = await self.session.execute(select(func.count()).select_from(owned))
        return int(result.scalar_one())

    async def count_tasks_by_status(self, user_id: uuid.UUID) -> dict[TaskStatus, int]:
        """`{status: count}` — one grouped query for the whole status breakdown."""
        owned = self._owned_tasks(user_id)
        result = await self.session.execute(
            select(owned.c.status, func.count()).select_from(owned).group_by(owned.c.status)
        )
        return {TaskStatus(status): int(count) for status, count in result.all()}

    async def count_tasks_by_priority(self, user_id: uuid.UUID) -> dict[TaskPriority, int]:
        """`{priority: count}` for the priority breakdown."""
        owned = self._owned_tasks(user_id)
        result = await self.session.execute(
            select(owned.c.priority, func.count()).select_from(owned).group_by(owned.c.priority)
        )
        return {TaskPriority(priority): int(count) for priority, count in result.all()}

    async def count_overdue(self, user_id: uuid.UUID) -> int:
        """Live, unfinished tasks whose due date has passed."""
        owned = self._owned_tasks(user_id)
        result = await self.session.execute(
            select(func.count())
            .select_from(owned)
            .where(
                owned.c.due_date.is_not(None),
                owned.c.due_date < datetime.now(UTC),
                owned.c.completed_at.is_(None),
                owned.c.status.in_(_OPEN_STATUSES),
            )
        )
        return int(result.scalar_one())

    async def count_comments(self, user_id: uuid.UUID) -> int:
        """Comments the user wrote on their own tasks."""
        result = await self.session.execute(
            select(func.count())
            .select_from(Comment)
            .where(
                Comment.task_id.in_(self._my_task_ids(user_id)),
                Comment.author_id == user_id,
                Comment.deleted_at.is_(None),
            )
        )
        return int(result.scalar_one())

    async def count_assigned_to_me(self, user_id: uuid.UUID) -> int:
        """Open tasks assigned to the user, in projects they do not necessarily own."""
        result = await self.session.execute(
            select(func.count())
            .select_from(Task)
            .where(
                Task.assignee_id == user_id,
                Task.deleted_at.is_(None),
                Task.status.in_(_OPEN_STATUSES),
            )
        )
        return int(result.scalar_one())

    async def average_completion_hours(self, user_id: uuid.UUID) -> float | None:
        """Mean hours between creation and completion for finished tasks."""
        owned = self._owned_tasks(user_id)
        result = await self.session.execute(
            select(
                func.avg(func.extract("epoch", owned.c.completed_at - owned.c.created_at) / 3600.0)
            )
            .select_from(owned)
            .where(owned.c.completed_at.is_not(None))
        )
        value = result.scalar_one()
        return round(float(value), 2) if value is not None else None
