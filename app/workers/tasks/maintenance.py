"""Periodic maintenance jobs driven by Celery beat.

Everything here is safe to run repeatedly and safe to run twice concurrently: the tasks
are either pure reads or idempotent bulk updates.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from celery import Task

from app.core.celery_app import celery_app
from app.core.constants import QUEUE_MAINTENANCE, QUEUE_NOTIFICATIONS
from app.core.logging import get_logger
from app.db.session import session_scope
from app.repositories.refresh_token import RefreshTokenRepository
from app.repositories.task import TaskRepository
from app.repositories.user import UserRepository
from app.workers.dispatch import enqueue

logger = get_logger(__name__)


@celery_app.task(
    bind=True,
    name="app.workers.tasks.maintenance.notify_overdue_tasks",
    max_retries=2,
    default_retry_delay=120,
    queue=QUEUE_MAINTENANCE,
)
async def notify_overdue_tasks(self: Task, *, limit: int = 500) -> dict[str, int]:
    """Announce every overdue task once, then mark it as announced.

    `overdue_notified_at` is the deduplication guard: without it the nightly job would
    mail the same assignee about the same task forever.
    """
    async with session_scope() as session:
        tasks = await TaskRepository(session).bulk_overdue(limit=limit)
        if not tasks:
            return {"tasks": 0, "notified_users": 0}

        assignees: dict[uuid.UUID, int] = {}
        now = datetime.now(UTC)
        for task in tasks:
            task.overdue_notified_at = now
            if task.assignee_id is not None:
                assignees[task.assignee_id] = assignees.get(task.assignee_id, 0) + 1
        announced = len(tasks)

    for user_id in assignees:
        await enqueue(
            "app.workers.tasks.notifications.send_overdue_digest",
            str(user_id),
            queue=QUEUE_NOTIFICATIONS,
        )

    logger.info("overdue_sweep_done", tasks=announced, users=len(assignees))
    return {"tasks": announced, "notified_users": len(assignees)}


@celery_app.task(
    bind=True,
    name="app.workers.tasks.maintenance.purge_expired_tokens",
    queue=QUEUE_MAINTENANCE,
)
async def purge_expired_tokens(self: Task, *, older_than_days: int = 30) -> dict[str, int]:
    """Delete refresh tokens that expired long ago."""
    async with session_scope() as session:
        removed = await RefreshTokenRepository(session).purge_expired(
            older_than_days=older_than_days
        )
    if removed:
        logger.info("expired_tokens_purged", removed=removed)
    return {"removed": removed}


@celery_app.task(
    bind=True,
    name="app.workers.tasks.maintenance.warm_stats_cache",
    queue=QUEUE_MAINTENANCE,
)
async def warm_stats_cache(self: Task, *, limit: int = 200) -> dict[str, int]:
    """Pre-compute dashboards for the most recently active users.

    A cache that is only ever filled by reads is a cache that is always cold right after
    a deploy; warming it here keeps the first dashboard load fast.
    """
    from app.services.stats import StatsService

    async with session_scope() as session:
        users = await UserRepository(session).find_all_by(deleted_at=None, is_active=True)
        service = StatsService(session)
        warmed = 0
        for user in users[:limit]:
            await service.overview(user=user, use_cache=True)
            warmed += 1
    return {"warmed": warmed}


@celery_app.task(
    bind=True,
    name="app.workers.tasks.maintenance.healthcheck",
    queue=QUEUE_MAINTENANCE,
)
async def healthcheck(self: Task) -> dict[str, bool]:
    """Report dependency health from inside the worker."""
    from sqlalchemy import text

    from app.core.cache import ping

    async with session_scope() as session:
        await session.execute(text("SELECT 1"))
    return {"database": True, "redis": await ping()}
