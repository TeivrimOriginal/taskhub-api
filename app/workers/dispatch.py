"""Thin wrapper around "put this task on the queue".

Used by the service layer (which must not care whether a broker exists) and by the beat
jobs.

The interesting part is `task_always_eager`: in a single-process deployment — and in the
test-suite — Celery is configured to run tasks inline. Two things then need care:

* `send_task()` raises `AlwaysEagerIgnored` in eager mode, so the registered task object
  is invoked directly instead;
* the tasks here are `async def`. `apply_async()` in eager mode wraps whatever the task
  returned into an `EagerResult` and never awaits it — for a coroutine that means "never
  awaited". Calling `task.run()` keeps the coroutine in our hands, so it is awaited right
  here, on the same loop and therefore the same database engine.
"""

from __future__ import annotations

import inspect
from typing import Any

from app.core.celery_app import celery_app
from app.core.logging import get_logger

logger = get_logger(__name__)


async def enqueue(task_name: str, *args: Any, queue: str = "default", **kwargs: Any) -> bool:
    """Run `task_name` now (eager) or hand it to the broker; `False` if it could not be.

    Never raises: a broker outage must not roll back the write that triggered the job.
    """
    try:
        if celery_app.conf.task_always_eager:
            task = celery_app.tasks.get(task_name)
            if task is None:
                logger.warning("enqueue_eager_task_unknown", task=task_name)
                return False
            outcome = task.run(*args, **kwargs)
            if inspect.isawaitable(outcome):
                await outcome
        else:
            celery_app.send_task(task_name, args=list(args), kwargs=kwargs or None, queue=queue)
    except Exception as exc:
        logger.warning("enqueue_failed", task=task_name, error=str(exc))
        return False

    logger.debug("enqueued", task=task_name, queue=queue)
    return True
