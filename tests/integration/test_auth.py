"""Registration, login, token rotation, revocation and the RBAC matrix."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import Role
from tests.conftest import TEST_PASSWORD

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.models.project import Project
    from app.models.user import User

pytestmark = pytest.mark.integration

STRONG_PASSWORD = "Str0ngPassw0rd!"


async def register(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    """Register a fresh account and return the JSON body."""
    unique = uuid.uuid4().hex[:8]
    payload: dict[str, Any] = {
        "email": f"user_{unique}@taskhub.dev",
        "username": f"user_{unique}",
        "password": STRONG_PASSWORD,
        "full_name": "Test User",
    }
    payload.update(overrides)
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


class TestRegistration:
    async def test_creates_an_account_and_returns_tokens(self, client: AsyncClient) -> None:
        body = await register(client)
        assert body["token_type"] == "bearer"
        assert body["access_token"]
        assert body["refresh_token"]
        assert body["expires_in"] == 1800
        assert body["user"]["email"]

    async def test_never_returns_the_password_hash(self, client: AsyncClient) -> None:
        body = await register(client)
        assert "password" not in str(body).lower() or "hashed_password" not in str(body)

    async def test_duplicate_email_is_rejected_case_insensitively(
        self, client: AsyncClient
    ) -> None:
        first = await register(client)
        unique = uuid.uuid4().hex[:8]
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": str(first["user"]["email"]).upper(),
                "username": f"other_{unique}",
                "password": STRONG_PASSWORD,
            },
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"

    async def test_duplicate_username_is_rejected(self, client: AsyncClient) -> None:
        first = await register(client)
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": f"other_{uuid.uuid4().hex[:8]}@taskhub.dev",
                "username": str(first["user"]["username"]),
                "password": STRONG_PASSWORD,
            },
        )
        assert response.status_code == 409

    async def test_weak_password_is_rejected(self, client: AsyncClient) -> None:
        unique = uuid.uuid4().hex[:8]
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": f"weak_{unique}@taskhub.dev",
                "username": f"weak_{unique}",
                "password": "alllowercase",
            },
        )
        assert response.status_code == 422
        assert "password" in response.json()["error"]["details"]

    async def test_missing_fields_are_reported_per_field(self, client: AsyncClient) -> None:
        response = await client.post("/api/v1/auth/register", json={})
        assert response.status_code == 422
        details = response.json()["error"]["details"]
        assert {"email", "username", "password"} <= set(details)


class TestLogin:
    async def test_json_login(self, client: AsyncClient) -> None:
        body = await register(client)
        response = await client.post(
            "/api/v1/auth/login-json",
            json={"username": body["user"]["email"], "password": STRONG_PASSWORD},
        )
        assert response.status_code == 200
        assert response.json()["access_token"]

    async def test_oauth2_form_login(self, client: AsyncClient) -> None:
        body = await register(client)
        response = await client.post(
            "/api/v1/auth/login",
            data={"username": body["user"]["username"], "password": STRONG_PASSWORD},
        )
        assert response.status_code == 200

    async def test_wrong_password_is_401(self, client: AsyncClient) -> None:
        body = await register(client)
        response = await client.post(
            "/api/v1/auth/login-json",
            json={"username": body["user"]["email"], "password": "WrongPass1!"},
        )
        assert response.status_code == 401

    async def test_unknown_user_and_wrong_password_are_indistinguishable(
        self, client: AsyncClient
    ) -> None:
        """The response must not reveal whether an account exists."""
        unknown = await client.post(
            "/api/v1/auth/login-json",
            json={"username": "nobody@taskhub.dev", "password": "Whatever123!"},
        )
        body = await register(client)
        wrong = await client.post(
            "/api/v1/auth/login-json",
            json={"username": body["user"]["email"], "password": "Whatever123!"},
        )
        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json()["error"]["message"] == wrong.json()["error"]["message"]

    async def test_inactive_account_cannot_log_in(
        self,
        client: AsyncClient,
        make_user: Callable[..., User],
        flush: Callable[[], Awaitable[None]],
    ) -> None:
        unique = uuid.uuid4().hex[:8]
        make_user(
            email=f"inactive_{unique}@taskhub.dev",
            username=f"inactive_{unique}",
            is_active=False,
        )
        await flush()
        response = await client.post(
            "/api/v1/auth/login-json",
            json={"username": f"inactive_{unique}@taskhub.dev", "password": TEST_PASSWORD},
        )
        assert response.status_code == 401
        assert "deactivated" in response.json()["error"]["message"].lower()

    async def test_login_records_last_login(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        body = await register(client)
        user_id = body["user"]["id"]
        from app.models.user import User

        user = await db_session.get(User, uuid.UUID(str(user_id)))
        assert user is not None
        assert user.last_login_at is not None


class TestTokenLifecycle:
    async def test_me_returns_the_current_user(self, logged_in: AsyncClient, user: User) -> None:
        response = await logged_in.get("/api/v1/auth/me")
        assert response.status_code == 200
        assert response.json()["id"] == str(user.id)
        assert "hashed_password" not in response.json()

    async def test_missing_token_is_401(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/projects")
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"

    async def test_garbage_token_is_401(self, client: AsyncClient) -> None:
        response = await client.get(
            "/api/v1/projects",
            headers={"Authorization": "Bearer not-a-jwt"},
        )
        assert response.status_code == 401

    async def test_refresh_token_is_rejected_as_an_access_token(
        self,
        client: AsyncClient,
        auth_headers: Callable[..., dict[str, str]],
        user: User,
    ) -> None:
        response = await client.get(
            "/api/v1/projects", headers=auth_headers(user, token_type="refresh")
        )
        assert response.status_code == 401

    async def test_rotation_issues_a_new_pair(self, client: AsyncClient) -> None:
        body = await register(client)
        response = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": body["refresh_token"]},
        )
        assert response.status_code == 200
        rotated = response.json()
        assert rotated["refresh_token"] != body["refresh_token"]

    async def test_reusing_a_consumed_refresh_token_is_rejected(self, client: AsyncClient) -> None:
        body = await register(client)
        stolen = body["refresh_token"]
        first = await client.post("/api/v1/auth/refresh", json={"refresh_token": stolen})
        assert first.status_code == 200

        replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": stolen})
        assert replay.status_code == 401

    async def test_reuse_revokes_the_whole_family(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        """Theft detection: the attacker's *and* the victim's tokens both die."""
        from app.models.refresh_token import RefreshToken

        body = await register(client)
        await client.post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
        await client.post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})

        rows = (
            (
                await db_session.execute(
                    select(RefreshToken).where(
                        RefreshToken.user_id == uuid.UUID(str(body["user"]["id"]))
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows, "no refresh token rows were persisted"
        assert all(row.revoked_at is not None for row in rows)

    async def test_logout_revokes_the_presented_token(
        self,
        client: AsyncClient,
        auth_headers: Callable[..., dict[str, str]],
        user: User,
    ) -> None:
        headers = auth_headers(user, token_type="refresh")
        response = await client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": headers["Authorization"].removeprefix("Bearer ")},
            headers=auth_headers(user),
        )
        assert response.status_code == 200

    async def test_logout_all_revokes_every_session(
        self,
        client: AsyncClient,
        auth_headers: Callable[..., dict[str, str]],
        user: User,
        db_session: AsyncSession,
    ) -> None:
        from app.models.refresh_token import RefreshToken

        for _ in range(3):
            await client.post(
                "/api/v1/auth/login-json",
                json={
                    "username": user.email,
                    "password": TEST_PASSWORD,
                },
            )

        response = await client.post("/api/v1/auth/logout-all", headers=auth_headers(user))
        assert response.status_code == 200
        assert response.json()["detail"]["revoked"] >= 3

        rows = (
            (await db_session.execute(select(RefreshToken).where(RefreshToken.user_id == user.id)))
            .scalars()
            .all()
        )
        assert all(row.revoked_at is not None for row in rows)

    async def test_change_password_requires_the_current_one(
        self,
        client: AsyncClient,
        auth_headers: Callable[..., dict[str, str]],
        user: User,
    ) -> None:
        response = await client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "WrongPass1!", "new_password": "N3wPassword!"},
            headers=auth_headers(user),
        )
        assert response.status_code == 401

    async def test_change_password_then_login_with_the_new_one(
        self,
        client: AsyncClient,
        auth_headers: Callable[..., dict[str, str]],
        user: User,
    ) -> None:
        response = await client.post(
            "/api/v1/auth/change-password",
            json={"current_password": TEST_PASSWORD, "new_password": "N3wPassword!"},
            headers=auth_headers(user),
        )
        assert response.status_code == 200

        with_new = await client.post(
            "/api/v1/auth/login-json",
            json={"username": user.email, "password": "N3wPassword!"},
        )
        assert with_new.status_code == 200

    async def test_expired_access_token_is_401(
        self,
        client: AsyncClient,
        auth_headers: Callable[..., dict[str, str]],
        user: User,
    ) -> None:
        headers = auth_headers(user, expires_delta=timedelta(seconds=-10))
        response = await client.get("/api/v1/auth/me", headers=headers)
        assert response.status_code == 401


class TestRbac:
    async def test_plain_user_cannot_list_accounts(self, logged_in: AsyncClient) -> None:
        response = await logged_in.get("/api/v1/users")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "permission_denied"

    async def test_moderator_can_list_accounts(self, as_staff: AsyncClient) -> None:
        response = await as_staff.get("/api/v1/users")
        assert response.status_code == 200

    async def test_moderator_cannot_create_accounts(self, as_staff: AsyncClient) -> None:
        unique = uuid.uuid4().hex[:8]
        response = await as_staff.post(
            "/api/v1/users",
            json={
                "email": f"made_{unique}@taskhub.dev",
                "username": f"made_{unique}",
                "password": STRONG_PASSWORD,
            },
        )
        assert response.status_code == 403

    async def test_admin_can_create_accounts(self, as_admin: AsyncClient) -> None:
        unique = uuid.uuid4().hex[:8]
        response = await as_admin.post(
            "/api/v1/users",
            params={"role": Role.MODERATOR.value},
            json={
                "email": f"made_{unique}@taskhub.dev",
                "username": f"made_{unique}",
                "password": STRONG_PASSWORD,
            },
        )
        assert response.status_code == 201
        assert response.json()["role"] == "moderator"

    async def test_admin_can_soft_delete_and_restore(
        self, as_admin: AsyncClient, user: User
    ) -> None:
        deleted = await as_admin.delete(f"/api/v1/users/{user.id}")
        assert deleted.status_code == 200

        hidden = await as_admin.get(f"/api/v1/users/{user.id}")
        assert hidden.status_code == 404

        restored = await as_admin.post(f"/api/v1/users/{user.id}/activate")
        assert restored.status_code == 200
        assert restored.json()["is_active"] is True

    async def test_a_user_can_read_their_own_profile(
        self, logged_in: AsyncClient, user: User
    ) -> None:
        response = await logged_in.get(f"/api/v1/users/{user.id}")
        assert response.status_code == 200

    async def test_a_user_cannot_read_a_foreign_profile(
        self,
        logged_in: AsyncClient,
        other_project: Project,
    ) -> None:
        from app.models.user import User as UserModel

        owner_id = other_project.owner_id
        response = await logged_in.get(f"/api/v1/users/{owner_id}")
        assert response.status_code == 403
        assert UserModel is not None

    async def test_a_user_cannot_patch_a_foreign_profile(
        self,
        logged_in: AsyncClient,
        other_project: Project,
    ) -> None:
        response = await logged_in.patch(
            f"/api/v1/users/{other_project.owner_id}",
            json={"full_name": "hijacked"},
        )
        assert response.status_code == 403

    async def test_a_user_may_patch_their_own_profile(
        self, logged_in: AsyncClient, user: User
    ) -> None:
        response = await logged_in.patch("/api/v1/auth/me", json={"full_name": "Renamed"})
        assert response.status_code == 200
        assert response.json()["full_name"] == "Renamed"

    async def test_a_plain_user_cannot_escalate_themselves(self, logged_in: AsyncClient) -> None:
        response = await logged_in.patch(
            "/api/v1/auth/me", json={"is_active": True, "role": "admin"}
        )
        assert response.status_code in {200, 422}
        if response.status_code == 200:
            assert response.json()["role"] != "admin"
