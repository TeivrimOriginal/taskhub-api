"""Periodic maintenance jobs driven by Celery beat.

Everything here is safe to run repeatedly and safe to run twice concurrently: the tasks
are either pure reads or idempotent bulk updates.

Like the notification jobs, the task functions are synchronous and hand the work to
`asyncio.run` — Celery's prefork pool cannot await a coroutine, and an `async def` task
would die in the result serialiser instead of doing its job. A test that needs the
behaviour awaits the `_*` helper directly, so the job runs on the test's own loop and the
shared engine stays bound to it.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from celery import Task

from app.core.celery_app import celery_app
from app.core.constants import QUEUE_MAINTENANCE
from app.core.logging import get_logger
from app.db.session import session_scope
from app.repositories.refresh_token import RefreshTokenRepository
from app.repositories.task import TaskRepository
from app.repositories.user import UserRepository

logger = get_logger(__name__)


@celery_app.task(
    bind=True,
    name="app.workers.tasks.maintenance.notify_overdue_tasks",
    max_retries=2,
    default_retry_delay=120,
    queue=QUEUE_MAINTENANCE,
)
def notify_overdue_tasks(self: Task, limit: int = 500) -> dict[str, int]:
    """Announce every overdue task once, then mark it as announced.

    `overdue_notified_at` is the deduplication guard: without it the nightly job would
    mail the same assignee about the same task forever.
    """
    return asyncio.run(_notify_overdue_tasks(limit=limit))


async def _notify_overdue_tasks(*, limit: int) -> dict[str, int]:
    """Body of `notify_overdue_tasks`."""
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
        try:
            celery_app.send_task(
                "app.workers.tasks.notifications.send_overdue_digest",
                args=[str(user_id)],
                queue="notifications",
            )
        except Exception as exc:
            # The rows are already marked as announced, so a lost digest is not retried;
            # failing the whole sweep instead would leave *every* other task unannounced.
            # `AlwaysEagerIgnored` shows up here too: `send_task` has no eager path, so
            # under `task_always_eager` (tests, one-off runs) it always raises.
            logger.warning("overdue_digest_enqueue_failed", user_id=str(user_id), error=str(exc))

    logger.info("overdue_sweep_done", tasks=announced, users=len(assignees))
    return {"tasks": announced, "notified_users": len(assignees)}


@celery_app.task(
    bind=True,
    name="app.workers.tasks.maintenance.purge_expired_tokens",
    queue=QUEUE_MAINTENANCE,
)
def purge_expired_tokens(self: Task, older_than_days: int = 30) -> dict[str, int]:
    """Delete refresh tokens that expired long ago."""
    return asyncio.run(_purge_expired_tokens(older_than_days=older_than_days))


async def _purge_expired_tokens(*, older_than_days: int) -> dict[str, int]:
    """Body of `purge_expired_tokens`."""
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
def warm_stats_cache(self: Task, limit: int = 200) -> dict[str, int]:
    """Pre-compute dashboards for the most recently active users.

    A cache that is only ever filled by reads is a cache that is always cold right after
    a deploy; warming it here keeps the first dashboard load fast.
    """
    return asyncio.run(_warm_stats_cache(limit=limit))


async def _warm_stats_cache(*, limit: int) -> dict[str, int]:
    """Body of `warm_stats_cache`."""
    from app.services.stats import StatsService

    async with session_scope() as session:
        users = await UserRepository(session).find_all_by(deleted_at=None, is_active=True)
        service = StatsService(session)
        for user in users[:limit]:
            await service.overview(user=user, use_cache=True)
    return {"warmed": min(len(users), limit)}


@celery_app.task(
    bind=True,
    name="app.workers.tasks.maintenance.healthcheck",
    queue=QUEUE_MAINTENANCE,
)
def healthcheck(self: Task) -> dict[str, bool]:
    """Report dependency health from inside the worker."""
    return asyncio.run(_healthcheck())


async def _healthcheck() -> dict[str, bool]:
    """Body of `healthcheck`: one trivial query plus a Redis ping."""
    from sqlalchemy import text

    from app.core.cache import ping

    async with session_scope() as session:
        await session.execute(text("SELECT 1"))
    return {"database": True, "redis": await ping()}
