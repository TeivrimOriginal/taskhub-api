"""Task lifecycle: creation, status transitions, filters, tags, comments, stats."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from httpx import AsyncClient

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

    from app.models.project import Project
    from app.models.tag import Tag
    from app.models.task import Task
    from app.models.user import User

pytestmark = pytest.mark.integration


async def create_task(
    client: AsyncClient,
    project: Project,
    **overrides: Any,
) -> dict[str, Any]:
    """Create a task through the API and return the JSON body."""
    payload: dict[str, Any] = {
        "project_id": str(project.id),
        "title": "Write the tests",
        "priority": "medium",
    }
    payload.update(overrides)
    response = await client.post("/api/v1/tasks", json=payload)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


class TestCreate:
    async def test_creates_a_task_with_defaults(
        self, logged_in: AsyncClient, project: Project
    ) -> None:
        body = await create_task(logged_in, project)
        assert body["status"] == "todo"
        assert body["priority"] == "medium"
        assert body["completed_at"] is None
        assert body["tags"] == []
        assert body["project"]["id"] == str(project.id)

    async def test_position_is_assigned_automatically(
        self,
        logged_in: AsyncClient,
        project: Project,
    ) -> None:
        first = await create_task(logged_in, project)
        second = await create_task(logged_in, project, title="Second")
        assert second["position"] == first["position"] + 1

    async def test_can_be_created_with_tags(
        self, logged_in: AsyncClient, project: Project, tag: Tag
    ) -> None:
        body = await create_task(logged_in, project, tag_ids=[str(tag.id)])
        assert [t["name"] for t in body["tags"]] == [tag.name]

    async def test_unknown_tag_is_rejected(self, logged_in: AsyncClient, project: Project) -> None:
        response = await logged_in.post(
            "/api/v1/tasks",
            json={"project_id": str(project.id), "title": "t", "tag_ids": [str(uuid.uuid4())]},
        )
        assert response.status_code == 422

    async def test_unknown_project_is_404(self, logged_in: AsyncClient) -> None:
        response = await logged_in.post(
            "/api/v1/tasks",
            json={"project_id": str(uuid.uuid4()), "title": "t"},
        )
        assert response.status_code == 404

    async def test_stranger_cannot_create_in_a_foreign_project(
        self,
        client: AsyncClient,
        project: Project,
        other_project: Project,
        token_for: Callable[..., str],
    ) -> None:
        client.headers.update({"Authorization": f"Bearer {token_for(other_project.owner_id)}"})
        response = await client.post(
            "/api/v1/tasks",
            json={"project_id": str(project.id), "title": "t"},
        )
        assert response.status_code == 403

    async def test_started_status_sets_started_at(
        self,
        logged_in: AsyncClient,
        project: Project,
    ) -> None:
        body = await create_task(logged_in, project, status="in_progress")
        assert body["started_at"] is not None

    async def test_creating_a_done_task_sets_completed_at(
        self,
        logged_in: AsyncClient,
        project: Project,
    ) -> None:
        body = await create_task(logged_in, project, status="done")
        assert body["completed_at"] is not None

    async def test_project_counter_increases(
        self, logged_in: AsyncClient, project: Project
    ) -> None:
        await create_task(logged_in, project)
        await create_task(logged_in, project)
        body = (await logged_in.get(f"/api/v1/projects/{project.id}")).json()
        assert body["tasks_total"] == 2


class TestRead:
    async def test_detail_includes_relations(
        self,
        logged_in: AsyncClient,
        task: Task,
        tag: Tag,
    ) -> None:
        response = await logged_in.get(f"/api/v1/tasks/{task.id}")
        assert response.status_code == 200
        body = response.json()
        assert body["project"]["name"]
        assert body["tags"] == []

    async def test_unknown_id_is_404(self, logged_in: AsyncClient) -> None:
        assert (await logged_in.get(f"/api/v1/tasks/{uuid.uuid4()}")).status_code == 404

    async def test_deleted_task_is_404(self, logged_in: AsyncClient, task: Task) -> None:
        await logged_in.delete(f"/api/v1/tasks/{task.id}")
        assert (await logged_in.get(f"/api/v1/tasks/{task.id}")).status_code == 404


class TestStatusTransitions:
    async def test_complete_sets_status_and_timestamp(
        self,
        logged_in: AsyncClient,
        task: Task,
    ) -> None:
        response = await logged_in.patch(f"/api/v1/tasks/{task.id}/complete")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "done"
        assert body["completed_at"] is not None

    async def test_reopening_clears_the_timestamp(
        self,
        logged_in: AsyncClient,
        task: Task,
    ) -> None:
        await logged_in.patch(f"/api/v1/tasks/{task.id}/complete")
        reopened = await logged_in.patch(f"/api/v1/tasks/{task.id}", json={"status": "todo"})
        assert reopened.json()["completed_at"] is None

    async def test_the_client_cannot_forge_completed_at(
        self,
        logged_in: AsyncClient,
        task: Task,
    ) -> None:
        """`completed_at` is derived server-side; the schema has no such field."""
        response = await logged_in.patch(
            f"/api/v1/tasks/{task.id}",
            json={"status": "done", "completed_at": "2020-01-01T00:00:00Z"},
        )
        assert response.status_code == 422

    async def test_completion_updates_the_project_counter(
        self,
        logged_in: AsyncClient,
        task: Task,
        project: Project,
    ) -> None:
        await logged_in.patch(f"/api/v1/tasks/{task.id}/complete")
        body = (await logged_in.get(f"/api/v1/projects/{project.id}")).json()
        assert body["tasks_done"] == 1
        assert body["completion_rate"] == 1.0

    async def test_priority_can_be_changed(self, logged_in: AsyncClient, task: Task) -> None:
        response = await logged_in.patch(f"/api/v1/tasks/{task.id}", json={"priority": "urgent"})
        assert response.json()["priority"] == "urgent"

    async def test_invalid_status_is_422(self, logged_in: AsyncClient, task: Task) -> None:
        response = await logged_in.patch(f"/api/v1/tasks/{task.id}", json={"status": "nope"})
        assert response.status_code == 422

    async def test_an_empty_patch_is_a_noop(self, logged_in: AsyncClient, task: Task) -> None:
        response = await logged_in.patch(f"/api/v1/tasks/{task.id}", json={})
        assert response.status_code == 200
        assert response.json()["title"] == task.title


class TestFiltering:
    @pytest.fixture(autouse=True)
    async def _seed(self, logged_in: AsyncClient, project: Project) -> None:
        await create_task(
            logged_in, project, title="Urgent backend fix", priority="urgent", status="todo"
        )
        await create_task(logged_in, project, title="Docs polish", priority="low", status="done")
        await create_task(
            logged_in, project, title="Refactor", priority="medium", status="in_progress"
        )

    async def test_filter_by_priority(self, logged_in: AsyncClient) -> None:
        response = await logged_in.get("/api/v1/tasks", params={"priority": "urgent"})
        assert response.json()["total"] == 1
        assert response.json()["items"][0]["title"] == "Urgent backend fix"

    async def test_filter_by_status(self, logged_in: AsyncClient) -> None:
        assert (await logged_in.get("/api/v1/tasks", params={"status": "done"})).json()[
            "total"
        ] == 1

    async def test_search_is_case_insensitive(self, logged_in: AsyncClient) -> None:
        assert (await logged_in.get("/api/v1/tasks", params={"search": "BACKEND"})).json()[
            "total"
        ] == 1
        assert (await logged_in.get("/api/v1/tasks", params={"search": "docs"})).json()[
            "total"
        ] == 1

    async def test_filters_combine_with_and(
        self,
        logged_in: AsyncClient,
        project: Project,
    ) -> None:
        response = await logged_in.get(
            "/api/v1/tasks",
            params={"priority": "urgent", "status": "todo", "search": "backend"},
        )
        assert response.json()["total"] == 1

    async def test_overdue_filter(
        self,
        logged_in: AsyncClient,
        project: Project,
    ) -> None:
        await create_task(
            logged_in,
            project,
            title="Late task",
            due_date=(datetime.now(UTC) - timedelta(days=2)).isoformat(),
        )
        await create_task(
            logged_in,
            project,
            title="Future task",
            due_date=(datetime.now(UTC) + timedelta(days=2)).isoformat(),
        )
        overdue = await logged_in.get("/api/v1/tasks", params={"overdue": "true"})
        assert overdue.json()["total"] == 1
        assert overdue.json()["items"][0]["title"] == "Late task"
        assert overdue.json()["items"][0]["is_overdue"] is True

    async def test_filter_by_project(self, logged_in: AsyncClient, project: Project) -> None:
        assert (
            await logged_in.get("/api/v1/tasks", params={"project_id": str(project.id)})
        ).json()["total"] == 3

    async def test_project_task_listing(self, logged_in: AsyncClient, project: Project) -> None:
        response = await logged_in.get(f"/api/v1/projects/{project.id}/tasks")
        assert response.status_code == 200
        assert response.json()["total"] == 3

    async def test_tag_filter(
        self,
        logged_in: AsyncClient,
        project: Project,
        tag: Tag,
    ) -> None:
        target = await create_task(logged_in, project, title="Tagged", tag_ids=[str(tag.id)])
        response = await logged_in.get("/api/v1/tasks", params={"tag": str(tag.id)})
        assert response.json()["total"] == 1
        assert response.json()["items"][0]["id"] == target["id"]


class TestTags:
    async def test_attach_and_detach(
        self,
        logged_in: AsyncClient,
        task: Task,
        tag: Tag,
    ) -> None:
        attached = await logged_in.post(
            f"/api/v1/tasks/{task.id}/tags", json={"tag_id": str(tag.id)}
        )
        assert attached.status_code == 200
        assert [t["id"] for t in attached.json()["tags"]] == [str(tag.id)]

        detached = await logged_in.delete(f"/api/v1/tasks/{task.id}/tags/{tag.id}")
        assert detached.status_code == 200
        assert detached.json()["tags"] == []

    async def test_attaching_twice_is_idempotent(
        self,
        logged_in: AsyncClient,
        task: Task,
        tag: Tag,
    ) -> None:
        await logged_in.post(f"/api/v1/tasks/{task.id}/tags", json={"tag_id": str(tag.id)})
        again = await logged_in.post(f"/api/v1/tasks/{task.id}/tags", json={"tag_id": str(tag.id)})
        assert again.status_code == 200
        assert len(again.json()["tags"]) == 1

    async def test_unknown_tag_is_404(
        self,
        logged_in: AsyncClient,
        task: Task,
    ) -> None:
        response = await logged_in.post(
            f"/api/v1/tasks/{task.id}/tags",
            json={"tag_id": str(uuid.uuid4())},
        )
        assert response.status_code == 404

    async def test_tag_listing(self, logged_in: AsyncClient, task: Task, tag: Tag) -> None:
        listed = (await logged_in.get(f"/api/v1/tasks/{task.id}/tags")).json()
        assert listed == []

    async def test_catalogue_creation_is_idempotent(self, logged_in: AsyncClient) -> None:
        first = await logged_in.post("/api/v1/tags", json={"name": "Backend"})
        second = await logged_in.post("/api/v1/tags", json={"name": "backend"})
        assert first.json()["id"] == second.json()["id"]

    async def test_tag_list_is_paginated_and_sorted(self, logged_in: AsyncClient) -> None:
        for name in ("zeta", "alpha", "mu"):
            await logged_in.post("/api/v1/tags", json={"name": name})
        body = (await logged_in.get("/api/v1/tags")).json()
        assert [item["name"] for item in body["items"]] == ["alpha", "mu", "zeta"]

    async def test_invalid_tag_name_is_422(self, logged_in: AsyncClient) -> None:
        assert (
            await logged_in.post("/api/v1/tags", json={"name": "Not Valid!"})
        ).status_code == 422

    async def test_only_staff_may_rename_or_delete(
        self,
        logged_in: AsyncClient,
        new_client: AsyncClient,
        admin_user: User,
        token_for: Callable[..., str],
        tag: Tag,
    ) -> None:
        """One client per identity: a single client carries a single bearer token."""
        new_client.headers.update({"Authorization": f"Bearer {token_for(admin_user)}"})
        assert (
            await new_client.patch(f"/api/v1/tags/{tag.id}", json={"name": "renamed"})
        ).status_code == 200
        assert (await new_client.delete(f"/api/v1/tags/{tag.id}")).status_code == 200

        # The plain user is stopped by the role check before the tag is even looked up,
        # so the answer is 403 (and never a hint about whether the tag exists).
        assert (
            await logged_in.patch(f"/api/v1/tags/{tag.id}", json={"name": "nope"})
        ).status_code == 403
        assert (await logged_in.delete(f"/api/v1/tags/{tag.id}")).status_code == 403

    async def test_popular_tags_require_staff(
        self,
        logged_in: AsyncClient,
        new_client: AsyncClient,
        admin_user: User,
        token_for: Callable[..., str],
    ) -> None:
        assert (await logged_in.get("/api/v1/tags/popular")).status_code == 403
        new_client.headers.update({"Authorization": f"Bearer {token_for(admin_user)}"})
        response = await new_client.get("/api/v1/tags/popular")
        assert response.status_code == 200
        assert isinstance(response.json(), list)


class TestComments:
    async def test_create_and_list(self, logged_in: AsyncClient, task: Task, user: User) -> None:
        created = await logged_in.post(
            f"/api/v1/tasks/{task.id}/comments",
            json={"body": "Looks good to me"},
        )
        assert created.status_code == 201
        assert created.json()["author"]["username"] == user.username

        listed = await logged_in.get(f"/api/v1/tasks/{task.id}/comments")
        assert listed.status_code == 200
        assert listed.json()["total"] == 1

    async def test_empty_comment_is_422(self, logged_in: AsyncClient, task: Task) -> None:
        response = await logged_in.post(f"/api/v1/tasks/{task.id}/comments", json={"body": "   "})
        assert response.status_code == 422

    async def test_author_can_edit(self, logged_in: AsyncClient, comment: Any) -> None:
        response = await logged_in.patch(
            f"/api/v1/comments/{comment.id}",
            json={"body": "Edited"},
        )
        assert response.status_code == 200
        assert response.json()["body"] == "Edited"

    async def test_a_stranger_cannot_edit_or_delete(
        self,
        new_client: AsyncClient,
        comment: Any,
        other_project: Project,
        token_for: Callable[..., str],
    ) -> None:
        new_client.headers.update({"Authorization": f"Bearer {token_for(other_project.owner_id)}"})
        assert (
            await new_client.patch(
                f"/api/v1/comments/{comment.id}",
                json={"body": "hijack"},
            )
        ).status_code == 403
        assert (await new_client.delete(f"/api/v1/comments/{comment.id}")).status_code == 403

    async def test_commenting_on_an_unknown_task_is_404(self, logged_in: AsyncClient) -> None:
        response = await logged_in.post(
            f"/api/v1/tasks/{uuid.uuid4()}/comments",
            json={"body": "hi"},
        )
        assert response.status_code == 404

    async def test_deleted_comment_disappears(self, logged_in: AsyncClient, comment: Any) -> None:
        assert (await logged_in.delete(f"/api/v1/comments/{comment.id}")).status_code == 200
        listed = await logged_in.get(f"/api/v1/tasks/{comment.task_id}/comments")
        assert listed.json()["total"] == 0


class TestDelete:
    async def test_owner_can_delete(
        self,
        logged_in: AsyncClient,
        task: Task,
        project: Project,
    ) -> None:
        assert (await logged_in.delete(f"/api/v1/tasks/{task.id}")).status_code == 200
        assert (await logged_in.get(f"/api/v1/tasks/{task.id}")).status_code == 404
        assert (await logged_in.get("/api/v1/tasks")).json()["total"] == 0
        assert (await logged_in.get(f"/api/v1/projects/{project.id}")).json()["tasks_total"] == 0

    async def test_stranger_cannot_delete(
        self,
        client: AsyncClient,
        task: Task,
        other_project: Project,
        token_for: Callable[..., str],
    ) -> None:
        client.headers.update({"Authorization": f"Bearer {token_for(other_project.owner_id)}"})
        assert (await client.delete(f"/api/v1/tasks/{task.id}")).status_code == 403

    async def test_admin_can_delete_any_task(self, as_admin: AsyncClient, task: Task) -> None:
        assert (await as_admin.delete(f"/api/v1/tasks/{task.id}")).status_code == 200


class TestStats:
    async def test_overview_counts_what_the_user_owns(
        self,
        logged_in: AsyncClient,
        project: Project,
    ) -> None:
        await create_task(logged_in, project, title="One")
        second = await create_task(logged_in, project, title="Two", status="done")
        await create_task(logged_in, project, title="Three", priority="urgent")

        await logged_in.patch(f"/api/v1/tasks/{second['id']}/complete")

        body = (await logged_in.get("/api/v1/stats/overview")).json()
        assert body["projects_total"] == 1
        assert body["tasks_total"] == 3
        assert body["tasks_done"] == 1
        assert body["by_status"]["done"] == 1
        assert body["by_priority"]["urgent"] == 1
        assert 0.0 < body["completion_rate"] < 1.0

    async def test_overview_is_empty_for_a_fresh_account(
        self,
        logged_in: AsyncClient,
    ) -> None:
        body = (await logged_in.get("/api/v1/stats/overview")).json()
        assert body["tasks_total"] == 0
        assert body["completion_rate"] == 0.0
        assert body["generated_at"]

    async def test_refresh_bypasses_the_cache(
        self, logged_in: AsyncClient, project: Project
    ) -> None:
        await create_task(logged_in, project, title="One")
        first = (await logged_in.get("/api/v1/stats/overview")).json()
        assert first["tasks_total"] == 1

        await create_task(logged_in, project, title="Two")
        cached = (await logged_in.get("/api/v1/stats/overview")).json()
        fresh = (await logged_in.get("/api/v1/stats/overview", params={"refresh": "true"})).json()
        assert fresh["tasks_total"] == 2
        assert cached["tasks_total"] in {1, 2}

    async def test_velocity(self, logged_in: AsyncClient, project: Project) -> None:
        body = await create_task(logged_in, project, title="Done")
        await logged_in.patch(f"/api/v1/tasks/{body['id']}/complete")
        response = await logged_in.get("/api/v1/stats/velocity")
        assert response.status_code == 200
        assert "assigned_open_tasks" in response.json()

    async def test_requires_authentication(self, client: AsyncClient) -> None:
        assert (await client.get("/api/v1/stats/overview")).status_code == 401
