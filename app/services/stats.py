"""Dashboard statistics service - cached, because it is read on every page load.

The cache itself lives in `app.core.cache.cached_value`, so this service only says what
the number is and never repeats the "hit / miss / Redis is down" branches.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cached_value, make_key
from app.core.config import Settings, get_settings
from app.core.constants import TaskPriority, TaskStatus
from app.core.logging import get_logger
from app.models.user import User
from app.repositories.stats import StatsRepository
from app.schemas.stats import PriorityBreakdown, StatsOverview, StatusBreakdown

logger = get_logger(__name__)


class StatsService:
    """Builds `StatsOverview` from grouped SQL and caches it in Redis."""

    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.stats = StatsRepository(session)

    async def overview(self, *, user: User, use_cache: bool = True) -> StatsOverview:
        """The dashboard for one user, with the cache handled by `cached_value`.

        `?refresh=true` on the endpoint arrives here as `use_cache=False`: it forces the
        aggregates to be built again, and the fresh result is stored for the next reader.
        """
        return await cached_value(
            make_key("stats", user.id),
            lambda: self._build(user.id),
            schema=StatsOverview,
            ttl=self.settings.cache_ttl_seconds,
            use_cache=use_cache,
        )

    async def _build(self, user_id: uuid.UUID) -> StatsOverview:
        """One pass per aggregate — the database does the arithmetic."""
        tasks_total = await self.stats.count_tasks(user_id)
        projects_total = await self.stats.count_projects(user_id)
        by_status = await self.stats.count_tasks_by_status(user_id)
        by_priority = await self.stats.count_tasks_by_priority(user_id)
        overdue = await self.stats.count_overdue(user_id)
        comments = await self.stats.count_comments(user_id)

        status_breakdown = StatusBreakdown(
            **{
                status.value: by_status.get(status, 0)
                for status in TaskStatus
                if status is not TaskStatus.CANCELLED
            },
            cancelled=by_status.get(TaskStatus.CANCELLED, 0),
        )
        priority_breakdown = PriorityBreakdown(
            **{priority.value: by_priority.get(priority, 0) for priority in TaskPriority}
        )
        done = by_status.get(TaskStatus.DONE, 0)

        return StatsOverview(
            projects_total=projects_total,
            tasks_total=tasks_total,
            tasks_done=done,
            tasks_overdue=overdue,
            comments_total=comments,
            completion_rate=round(done / tasks_total, 4) if tasks_total else 0.0,
            by_status=status_breakdown,
            by_priority=priority_breakdown,
            generated_at=datetime.now(UTC),
        )

    async def average_completion_hours(self, user_id: uuid.UUID) -> float | None:
        """Mean time-to-completion, `None` when nothing has been finished yet."""
        return await self.stats.average_completion_hours(user_id)

    async def count_assigned_to_me(self, user_id: uuid.UUID) -> int:
        """Open tasks assigned to the user across other people's projects."""
        return await self.stats.count_assigned_to_me(user_id)
