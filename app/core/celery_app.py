"""Celery application.

One instance, three queues:

* ``default``       — short jobs enqueued from a request;
* ``notifications`` — e-mails, kept apart so a slow SMTP server cannot stall a worker;
* ``maintenance``   — periodic sweeps from the beat schedule.

Jobs are discovered by name, so adding `app/workers/tasks/<module>.py` is enough to make
its tasks available; there is no central registry to forget to update.
"""

from __future__ import annotations

from celery import Celery
from celery.signals import task_failure, task_postrun, task_prerun

from app.core.config import get_settings
from app.core.constants import QUEUE_DEFAULT, QUEUE_MAINTENANCE, QUEUE_NOTIFICATIONS
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

celery_app = Celery(
    "taskhub",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks.notifications", "app.workers.tasks.maintenance"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,  # redeliver a task if the worker dies mid-flight
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    result_expires=3600,
    task_default_queue=QUEUE_DEFAULT,
    task_routes={
        "app.workers.tasks.notifications.*": {"queue": QUEUE_NOTIFICATIONS},
        "app.workers.tasks.maintenance.*": {"queue": QUEUE_MAINTENANCE},
    },
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=settings.celery_task_always_eager,
    beat_schedule={
        "sweep-overdue-tasks": {
            "task": "app.workers.tasks.maintenance.notify_overdue_tasks",
            "schedule": 60 * 60 * 24,  # every 24 hours
        },
        "purge-expired-tokens": {
            "task": "app.workers.tasks.maintenance.purge_expired_tokens",
            "schedule": 60 * 60 * 6,  # every 6 hours
        },
        "refresh-stats-cache": {
            "task": "app.workers.tasks.maintenance.warm_stats_cache",
            "schedule": 60 * 10,  # every 10 minutes
        },
    },
)


@task_prerun.connect
def _log_task_start(task_id: str | None = None, task: object = None, **_: object) -> None:
    logger.info("celery_task_started", task_id=task_id, task=getattr(task, "name", "?"))


@task_postrun.connect
def _log_task_end(task_id: str | None = None, task: object = None, **_: object) -> None:
    logger.info("celery_task_finished", task_id=task_id, task=getattr(task, "name", "?"))


@task_failure.connect
def _log_task_failure(
    task_id: str | None = None, exception: BaseException | None = None, **_: object
) -> None:
    logger.error("celery_task_failed", task_id=task_id, error=str(exception))
