"""Project CRUD, ownership rules and the pagination envelope."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from httpx import AsyncClient

if TYPE_CHECKING:
    from typing import Any

    from app.models.project import Project
    from app.models.user import User

pytestmark = pytest.mark.integration


class TestCreate:
    async def test_creates_a_project_owned_by_the_caller(
        self,
        logged_in: AsyncClient,
        user: User,
    ) -> None:
        response = await logged_in.post(
            "/api/v1/projects",
            json={"name": "Release 1.0", "description": "Ship it", "color": "#4f46e5"},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["owner_id"] == str(user.id)
        assert body["name"] == "Release 1.0"
        assert body["tasks_total"] == 0
        assert body["completion_rate"] == 0.0

    async def test_client_cannot_choose_the_owner(
        self,
        logged_in: AsyncClient,
        user: User,
        other_project: Project,
    ) -> None:
        """`owner_id` is not part of the create schema — sending it is a 422, not a spoof."""
        response = await logged_in.post(
            "/api/v1/projects",
            json={"name": "Spoofed", "owner_id": str(other_project.owner_id)},
        )
        assert response.status_code == 422
        assert "owner_id" in response.json()["error"]["details"]

    async def test_rejects_a_blank_name(self, logged_in: AsyncClient) -> None:
        response = await logged_in.post("/api/v1/projects", json={"name": "   "})
        assert response.status_code == 422

    async def test_rejects_an_invalid_color(self, logged_in: AsyncClient) -> None:
        response = await logged_in.post("/api/v1/projects", json={"name": "x", "color": "blue"})
        assert response.status_code == 422

    async def test_requires_authentication(self, client: AsyncClient) -> None:
        response = await client.post("/api/v1/projects", json={"name": "x"})
        assert response.status_code == 401


class TestRead:
    async def test_detail(self, logged_in: AsyncClient, project: Project) -> None:
        response = await logged_in.get(f"/api/v1/projects/{project.id}")
        assert response.status_code == 200
        assert response.json()["id"] == str(project.id)

    async def test_unknown_id_is_404(self, logged_in: AsyncClient) -> None:
        response = await logged_in.get(f"/api/v1/projects/{uuid.uuid4()}")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    async def test_malformed_id_is_422(self, logged_in: AsyncClient) -> None:
        response = await logged_in.get("/api/v1/projects/not-a-uuid")
        assert response.status_code == 422
        assert "project_id" in response.json()["error"]["details"]


class TestList:
    async def test_only_lists_your_own_projects(
        self,
        logged_in: AsyncClient,
        project: Project,
        other_project: Project,
    ) -> None:
        response = await logged_in.get("/api/v1/projects")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == str(project.id)

    async def test_staff_sees_every_project(
        self,
        as_admin: AsyncClient,
        project: Project,
        other_project: Project,
    ) -> None:
        response = await as_admin.get("/api/v1/projects")
        assert response.json()["total"] == 2

    async def test_search_filters_by_name(
        self,
        logged_in: AsyncClient,
        project: Project,
    ) -> None:
        await logged_in.post("/api/v1/projects", json={"name": "Something else"})

        hit = await logged_in.get("/api/v1/projects", params={"search": "Test"})
        assert hit.json()["total"] == 1
        assert hit.json()["items"][0]["id"] == str(project.id)

        miss = await logged_in.get("/api/v1/projects", params={"search": "nothing-here"})
        assert miss.json()["total"] == 0

    async def test_filter_by_active_flag(
        self,
        logged_in: AsyncClient,
        project: Project,
    ) -> None:
        await logged_in.patch(f"/api/v1/projects/{project.id}", json={"is_active": False})
        assert (await logged_in.get("/api/v1/projects", params={"is_active": True})).json()[
            "total"
        ] == 0
        assert (await logged_in.get("/api/v1/projects", params={"is_active": False})).json()[
            "total"
        ] == 1

    async def test_pagination_envelope(
        self,
        logged_in: AsyncClient,
        project: Project,
    ) -> None:
        for index in range(5):
            await logged_in.post("/api/v1/projects", json={"name": f"Project {index}"})

        first = (await logged_in.get("/api/v1/projects", params={"page": 1, "size": 2})).json()
        assert first["total"] == 6
        assert first["pages"] == 3
        assert len(first["items"]) == 2
        assert first["has_next"] is True
        assert first["has_prev"] is False

        last = (await logged_in.get("/api/v1/projects", params={"page": 3, "size": 2})).json()
        assert len(last["items"]) == 2
        assert last["has_next"] is False
        assert last["has_prev"] is True

    async def test_page_size_is_capped(self, logged_in: AsyncClient) -> None:
        response = await logged_in.get("/api/v1/projects", params={"size": 1000})
        assert response.status_code == 422

    async def test_page_zero_is_rejected(self, logged_in: AsyncClient) -> None:
        assert (await logged_in.get("/api/v1/projects", params={"page": 0})).status_code == 422


class TestUpdate:
    async def test_owner_can_update(self, logged_in: AsyncClient, project: Project) -> None:
        response = await logged_in.patch(
            f"/api/v1/projects/{project.id}",
            json={"name": "Renamed", "description": "new description"},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Renamed"

    async def test_a_stranger_cannot_update(
        self,
        client: AsyncClient,
        project: Project,
        other_project: Project,
    ) -> None:
        owner_id = other_project.owner_id

        from tests.conftest import issue_token

        client.headers.update({"Authorization": f"Bearer {issue_token(owner_id)}"})
        response = await client.patch(f"/api/v1/projects/{project.id}", json={"name": "hijacked"})
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "permission_denied"

    async def test_admin_can_update_any_project(
        self,
        as_admin: AsyncClient,
        project: Project,
    ) -> None:
        response = await as_admin.patch(
            f"/api/v1/projects/{project.id}", json={"name": "Moderated"}
        )
        assert response.status_code == 200

    async def test_empty_patch_is_a_noop(self, logged_in: AsyncClient, project: Project) -> None:
        response = await logged_in.patch(f"/api/v1/projects/{project.id}", json={})
        assert response.status_code == 200
        assert response.json()["name"] == project.name


class TestDelete:
    async def test_owner_can_delete_and_it_disappears(
        self,
        logged_in: AsyncClient,
        project: Project,
    ) -> None:
        assert (await logged_in.delete(f"/api/v1/projects/{project.id}")).status_code == 200
        assert (await logged_in.get(f"/api/v1/projects/{project.id}")).status_code == 404

    async def test_a_stranger_cannot_delete(
        self,
        client: AsyncClient,
        project: Project,
        other_project: Project,
    ) -> None:
        from tests.conftest import issue_token

        client.headers.update({"Authorization": f"Bearer {issue_token(other_project.owner_id)}"})
        response = await client.delete(f"/api/v1/projects/{project.id}")
        assert response.status_code == 403

    async def test_deleting_a_project_also_hides_its_tasks(
        self,
        logged_in: AsyncClient,
        project: Project,
        task: Any,
    ) -> None:
        assert (await logged_in.delete(f"/api/v1/projects/{project.id}")).status_code == 200
        tasks = await logged_in.get("/api/v1/tasks")
        assert tasks.json()["total"] == 0

    async def test_deleting_twice_is_404(self, logged_in: AsyncClient, project: Project) -> None:
        await logged_in.delete(f"/api/v1/projects/{project.id}")
        assert (await logged_in.delete(f"/api/v1/projects/{project.id}")).status_code == 404
