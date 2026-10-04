"""Notification jobs.

Each job resolves its own recipients from the database, so enqueuing only needs an id.
Jobs are idempotent where it matters: the overdue sweep records when a task was last
announced and therefore never sends the same reminder twice.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from celery import Task
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.celery_app import celery_app
from app.core.constants import QUEUE_NOTIFICATIONS
from app.core.logging import get_logger
from app.db.session import session_scope
from app.repositories.task import TaskRepository
from app.repositories.user import UserRepository
from app.services.email import EmailService

logger = get_logger(__name__)


@celery_app.task(
    bind=True,
    name="app.workers.tasks.notifications.send_task_assigned",
    max_retries=3,
    default_retry_delay=30,
    queue=QUEUE_NOTIFICATIONS,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_jitter=True,
)
async def send_task_assigned(self: Task, task_id: str) -> dict[str, str]:
    """Mail the assignee of a freshly created task."""
    async with session_scope() as session:
        payload = await _assignment_payload(session, uuid.UUID(task_id))
    if payload is None:
        logger.info("assignment_notification_skipped", task_id=task_id, reason="no_assignee")
        return {"status": "skipped"}

    sent = await EmailService().send_task_assigned(
        to=payload["to"],
        task_title=payload["title"],
        project_name=payload["project"],
    )
    return {"status": "sent" if sent else "failed", "to": payload["to"]}


@celery_app.task(
    bind=True,
    name="app.workers.tasks.notifications.send_overdue_digest",
    max_retries=2,
    default_retry_delay=60,
    queue=QUEUE_NOTIFICATIONS,
)
async def send_overdue_digest(self: Task, user_id: str) -> dict[str, int | str]:
    """Send one digest listing every overdue task of a user."""
    async with session_scope() as session:
        user = await UserRepository(session).get_alive(uuid.UUID(user_id))
        if user is None:
            return {"status": "skipped", "reason": "unknown_user", "sent": 0}

        overdue = await _overdue_of_user(session, user.id)
        if not overdue:
            return {"status": "skipped", "reason": "nothing_overdue", "sent": 0}

        sent = await EmailService().send_overdue_digest(
            to=user.email,
            lines=overdue,
        )
    return {"status": "sent" if sent else "failed", "sent": int(sent), "to": user.email}


async def _assignment_payload(session: AsyncSession, task_id: uuid.UUID) -> dict[str, str] | None:
    """Collect what the template needs; all DB work stays inside the session scope."""
    tasks = TaskRepository(session)
    task = await tasks.get_alive(task_id)
    if task is None or task.assignee_id is None:
        return None
    assignee = await UserRepository(session).get_alive(task.assignee_id)
    if assignee is None:
        return None
    return {
        "to": assignee.email,
        "title": task.title,
        "project": task.project.name if task.project is not None else "TaskHub",
    }


async def _overdue_of_user(session: AsyncSession, user_id: uuid.UUID) -> list[str]:
    """Human-readable lines for the digest: `Title (project) — due <date>`."""
    tasks = await TaskRepository(session).bulk_overdue(limit=1000)
    lines: list[str] = []
    now = datetime.now(UTC)
    for task in tasks:
        if task.assignee_id != user_id or task.due_date is None:
            continue
        project_name = task.project.name if task.project is not None else "TaskHub"
        days_late = (now - task.due_date).days
        lines.append(f"«{task.title}» ({project_name}) — {days_late} day(s) overdue")
    return lines
