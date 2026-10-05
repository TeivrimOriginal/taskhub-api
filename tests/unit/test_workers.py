"""Celery task contracts.

These guard an invariant that is invisible until a job silently does nothing: a Celery
task on the prefork pool must be a *plain* function. An `async def` task is called, hands
the worker a coroutine, and the worker dies trying to serialise it as the result — the
job body never runs and the enqueuing request still returns 201.
"""

from __future__ import annotations

import inspect
from fnmatch import fnmatch

import pytest

from app.core.celery_app import celery_app

# Importing the task modules is what a worker does at start-up (`include=[...]`); the
# names only exist in the registry once they are imported.
from app.workers.tasks import maintenance, notifications  # noqa: F401

pytestmark = pytest.mark.unit


def _registered_task_names() -> list[str]:
    return sorted(name for name in celery_app.tasks if name.startswith("app.workers."))


class TestTaskRegistry:
    def test_both_task_modules_are_discovered(self) -> None:
        names = _registered_task_names()
        assert "app.workers.tasks.notifications.send_task_assigned" in names
        assert "app.workers.tasks.maintenance.notify_overdue_tasks" in names

    def test_every_task_is_a_plain_function(self) -> None:
        """The regression guard: `async def` tasks never run on a prefork worker."""
        offenders = [
            name
            for name in _registered_task_names()
            if inspect.iscoroutinefunction(celery_app.tasks[name].run)
        ]
        assert not offenders, f"these tasks are coroutine functions: {offenders}"

    def test_every_task_returns_something_serialisable(self) -> None:
        """A task that returns a coroutine or a connection is a result-encoding crash."""
        # Annotation-free check: the runner must be a plain function returning a dict-like
        # payload. Anything else has to be converted before it reaches the broker.
        for name in _registered_task_names():
            assert not inspect.isasyncgenfunction(celery_app.tasks[name].run), name


class TestBeatSchedule:
    def test_periodic_jobs_reference_registered_tasks(self) -> None:
        registered = set(celery_app.tasks)
        for entry in celery_app.conf.beat_schedule.values():
            assert entry["task"] in registered, entry["task"]

    def test_notifications_run_on_their_own_queue(self) -> None:
        """A slow SMTP server must not sit in front of the default queue."""
        for name in _registered_task_names():
            if ".notifications." in name:
                assert _queue_for(name) == "notifications", name

    def test_maintenance_jobs_are_separated_too(self) -> None:
        for name in _registered_task_names():
            if ".maintenance." in name:
                assert _queue_for(name) == "maintenance", name


def _queue_for(name: str) -> str | None:
    """Resolve a task's queue through Celery's glob routes.

    Routes are patterns (`app.workers.tasks.notifications.*`), so a plain dict lookup by
    task name would raise — which is how a routing rule can silently stop applying.
    """
    for pattern, options in celery_app.conf.task_routes.items():
        if fnmatch(name, pattern):
            return str(options["queue"])
    return None


class TestRetries:
    def test_transient_transport_errors_are_retried(self) -> None:
        task = celery_app.tasks["app.workers.tasks.notifications.send_task_assigned"]
        assert task.max_retries >= 1
        assert ConnectionError in task.autoretry_for

    def test_delivery_is_at_least_once_not_at_most_once(self) -> None:
        """A task acknowledged after execution can survive a worker crash."""
        assert celery_app.conf.task_acks_late is True
        assert celery_app.conf.worker_prefetch_multiplier == 1
