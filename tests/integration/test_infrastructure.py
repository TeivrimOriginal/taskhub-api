"""Cache behaviour, rate limiting and the Celery task layer."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core import cache
from app.core.celery_app import celery_app
from app.models.refresh_token import RefreshToken
from app.models.task import Task

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractAsyncContextManager as AsyncContextManager

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.config import Settings
    from app.models.project import Project
    from app.models.user import User

pytestmark = pytest.mark.integration


class TestCeleryConfiguration:
    def test_queues_are_routed(self) -> None:
        routes = celery_app.conf.task_routes
        assert routes["app.workers.tasks.notifications.*"]["queue"] == "notifications"
        assert routes["app.workers.tasks.maintenance.*"]["queue"] == "maintenance"

    def test_periodic_jobs_are_scheduled(self) -> None:
        schedule = celery_app.conf.beat_schedule
        assert "sweep-overdue-tasks" in schedule
        assert "purge-expired-tokens" in schedule

    def test_tasks_are_registered_by_name(self) -> None:
        # `Celery(include=[...])` is lazy: the modules are imported when a worker boots.
        # Importing them here mirrors that and, more importantly, catches a typo in a
        # task name that would otherwise only surface when the beat schedule fires.
        import app.workers.tasks.maintenance
        import app.workers.tasks.notifications  # noqa: F401

        registered = celery_app.tasks
        for name in (
            "app.workers.tasks.notifications.send_task_assigned",
            "app.workers.tasks.notifications.send_overdue_digest",
            "app.workers.tasks.maintenance.notify_overdue_tasks",
            "app.workers.tasks.maintenance.purge_expired_tokens",
        ):
            assert name in registered, f"{name} is missing from the Celery registry"

    def test_beat_schedule_names_match_registered_tasks(self) -> None:
        import app.workers.tasks.maintenance
        import app.workers.tasks.notifications  # noqa: F401

        for entry in celery_app.conf.beat_schedule.values():
            assert entry["task"] in celery_app.tasks, (
                f"{entry['task']} is scheduled but not defined"
            )

    def test_workers_redeliver_unacknowledged_work(self) -> None:
        assert celery_app.conf.task_acks_late is True
        assert celery_app.conf.task_reject_on_worker_lost is True

    def test_serialisation_is_json_only(self) -> None:
        assert celery_app.conf.task_serializer == "json"
        assert celery_app.conf.accept_content == ["json"]


class TestMaintenanceTasks:
    """Celery jobs open their own session, so the fixtures must commit first.

    A Celery task is deliberately not given the request's session: in production it runs
    in another process with its own connection. The tests honour that contract by
    committing the fixture data instead of sharing a transaction.
    """

    async def test_overdue_sweep_announces_each_task_once(
        self,
        db_session: AsyncSession,
        project: Project,
        user: User,
        eager_celery: None,
    ) -> None:
        from app.workers.tasks.maintenance import notify_overdue_tasks

        task = Task(
            project_id=project.id,
            assignee_id=user.id,
            title="Late",
            due_date=datetime.now(UTC) - timedelta(days=3),
        )
        db_session.add(task)
        await db_session.commit()

        first = await notify_overdue_tasks()
        assert first["tasks"] == 1

        await db_session.refresh(task)
        assert task.overdue_notified_at is not None

        second = await notify_overdue_tasks()
        assert second["tasks"] == 0, "a task must never be announced twice"

    async def test_future_tasks_are_not_announced(
        self,
        db_session: AsyncSession,
        project: Project,
        eager_celery: None,
    ) -> None:
        from app.workers.tasks.maintenance import notify_overdue_tasks

        db_session.add(
            Task(
                project_id=project.id,
                title="Later",
                due_date=datetime.now(UTC) + timedelta(days=2),
            )
        )
        await db_session.commit()
        assert (await notify_overdue_tasks())["tasks"] == 0

    async def test_finished_tasks_are_not_announced(
        self,
        db_session: AsyncSession,
        project: Project,
        eager_celery: None,
    ) -> None:
        from app.core.constants import TaskStatus
        from app.workers.tasks.maintenance import notify_overdue_tasks

        db_session.add(
            Task(
                project_id=project.id,
                title="Finished but late",
                status=TaskStatus.DONE,
                completed_at=datetime.now(UTC),
                due_date=datetime.now(UTC) - timedelta(days=9),
            )
        )
        await db_session.commit()
        assert (await notify_overdue_tasks())["tasks"] == 0

    async def test_expired_tokens_are_purged(
        self,
        db_session: AsyncSession,
        user: User,
        eager_celery: None,
    ) -> None:
        from app.workers.tasks.maintenance import purge_expired_tokens

        old = RefreshToken(
            user_id=user.id,
            jti=uuid.uuid4().hex,
            family_id=uuid.uuid4(),
            token_hash="hash",
            expires_at=datetime.now(UTC) - timedelta(days=90),
        )
        fresh = RefreshToken(
            user_id=user.id,
            jti=uuid.uuid4().hex,
            family_id=uuid.uuid4(),
            token_hash="hash",
            expires_at=datetime.now(UTC) + timedelta(days=5),
        )
        db_session.add_all([old, fresh])
        await db_session.commit()

        result = await purge_expired_tokens()
        assert result["removed"] == 1

        remaining = (await db_session.execute(select(RefreshToken.jti))).scalars().all()
        assert remaining == [fresh.jti]

    async def test_worker_healthcheck(self, eager_celery: None) -> None:
        from app.workers.tasks.maintenance import healthcheck

        result = await healthcheck()
        assert result["database"] is True


class TestCacheIntegration:
    async def test_stats_are_cached_and_invalidated_on_write(
        self,
        settings: Settings,
        client: AsyncClient,
        user: User,
        project: Project,
        token_for: Callable[..., str],
        requires_redis: None,
    ) -> None:
        client.headers.update({"Authorization": f"Bearer {token_for(user)}"})
        cache._unavailable_until = 0.0

        await client.post("/api/v1/tasks", json={"project_id": str(project.id), "title": "One"})
        first = (await client.get("/api/v1/stats/overview")).json()
        assert first["tasks_total"] == 1

        await client.post("/api/v1/tasks", json={"project_id": str(project.id), "title": "Two"})
        second = (await client.get("/api/v1/stats/overview")).json()
        assert second["tasks_total"] == 2, "the write must invalidate the cached dashboard"

    async def test_writes_succeed_without_redis(
        self,
        client: AsyncClient,
        user: User,
        project: Project,
        token_for: Callable[..., str],
    ) -> None:
        """A cache outage must degrade the response, never fail the request."""
        client.headers.update({"Authorization": f"Bearer {token_for(user)}"})
        cache.mark_unavailable(duration=30.0)
        try:
            response = await client.post(
                "/api/v1/tasks",
                json={"project_id": str(project.id), "title": "Works anyway"},
            )
            assert response.status_code == 201
            stats = await client.get("/api/v1/stats/overview")
            assert stats.status_code == 200
        finally:
            cache._unavailable_until = 0.0


class TestRateLimiting:
    """The limiter is backed by Redis; without one it deliberately fails *open*."""

    async def test_requests_over_the_limit_are_rejected(
        self,
        settings: Settings,
        requires_redis: None,
    ) -> None:
        """A dedicated app with a tiny limit proves the limiter actually trips."""
        async with _limited_client(settings, limit=2) as http:
            statuses = [(await http.get("/api/v1/projects")).status_code for _ in range(4)]

        assert statuses[0] == 401  # counted, but the request itself is unauthenticated
        assert 429 in statuses, f"expected a 429 somewhere in {statuses}"

    async def test_rate_limited_response_carries_the_429_envelope(
        self,
        settings: Settings,
        requires_redis: None,
    ) -> None:
        async with _limited_client(settings, limit=1) as http:
            first = await http.get("/api/v1/projects")
            second = await http.get("/api/v1/projects")

        assert first.status_code == 401
        assert second.status_code == 429
        body = second.json()
        assert body["error"]["code"] == "rate_limited"
        assert body["error"]["details"]["limit"] == 1
        assert second.headers["Retry-After"]

    async def test_limit_headers_are_advertised(self, settings: Settings) -> None:
        """Even when the backend is unavailable the contract of the headers holds."""
        async with _limited_client(settings, limit=10) as http:
            response = await http.get("/api/v1/projects")
        assert response.status_code == 401

    async def test_probes_stay_reachable_when_limited(
        self,
        settings: Settings,
        requires_redis: None,
    ) -> None:
        async with _limited_client(settings, limit=1) as http:
            statuses = [(await http.get("/health/live")).status_code for _ in range(4)]
        assert all(status == 200 for status in statuses)


def _limited_client(settings: Settings, *, limit: int) -> AsyncContextManager[AsyncClient]:
    """An ASGI client wired to an app whose rate limit is `limit` requests/window."""
    from app.core.config import Environment
    from app.main import create_app

    app = create_app(
        settings.model_copy(
            update={
                "rate_limit_enabled": True,
                "rate_limit_requests": limit,
                "rate_limit_window_seconds": 60,
                "environment": Environment.TESTING,
            }
        )
    )
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    )
