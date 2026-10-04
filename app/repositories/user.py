"""User queries."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import Role
from app.models.user import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    """Access to the `users` table."""

    model = User

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_alive(self, user_id: uuid.UUID) -> User | None:
        """Fetch a user that has not been soft-deleted."""
        return await self.find_one_by(id=user_id, deleted_at=None)

    async def get_by_email_or_username(self, identifier: str) -> User | None:
        """Resolve a login identifier — e-mail or username, case-insensitively.

        Both columns are `citext`, so the comparison is already case-insensitive in
        PostgreSQL and no `lower()` wrapper is needed here.
        """
        lowered = identifier.strip()
        return await self.find_one_by(email=lowered, deleted_at=None) or await self.find_one_by(
            username=lowered,
            deleted_at=None,
        )

    async def email_taken(self, email: str, *, exclude_id: uuid.UUID | None = None) -> bool:
        """Check e-mail uniqueness, optionally ignoring one user (for self-update)."""
        stmt = select(User.id).where(User.email == email.strip(), User.deleted_at.is_(None))
        if exclude_id is not None:
            stmt = stmt.where(User.id != exclude_id)
        result = await self.session.execute(stmt.limit(1))
        return result.scalar_one_or_none() is not None

    async def username_taken(self, username: str, *, exclude_id: uuid.UUID | None = None) -> bool:
        """Check username uniqueness, optionally ignoring one user."""
        stmt = select(User.id).where(User.username == username.strip(), User.deleted_at.is_(None))
        if exclude_id is not None:
            stmt = stmt.where(User.id != exclude_id)
        result = await self.session.execute(stmt.limit(1))
        return result.scalar_one_or_none() is not None

    def make_listing(
        self,
        *,
        search: str | None = None,
        role: Role | None = None,
        is_active: bool | None = None,
        include_deleted: bool = False,
    ) -> Select[Any]:
        """Build the filtered `SELECT` used by `GET /users`."""
        stmt = select(User)
        if not include_deleted:
            stmt = stmt.where(User.deleted_at.is_(None))
        if role is not None:
            stmt = stmt.where(User.role == role)
        if is_active is not None:
            stmt = stmt.where(User.is_active.is_(is_active))
        if search:
            pattern = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    User.email.ilike(pattern),
                    User.username.ilike(pattern),
                    User.full_name.ilike(pattern),
                )
            )
        return stmt.order_by(User.created_at.desc())

    async def touch_login(self, user: User, *, when: datetime | None = None) -> None:
        """Record a successful login (used by the auth service)."""
        user.last_login_at = when or datetime.now(UTC)
        await self.session.flush()

    async def count_all(self, *, include_deleted: bool = False) -> int:
        """Total number of accounts (staff-only statistic)."""
        stmt = select(func.count()).select_from(User).where(User.deleted_at.is_(None))
        if include_deleted:
            stmt = select(func.count()).select_from(User)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def list_by_role(self, role: Role, **filters: Any) -> list[User]:
        """All users holding a given role — used by the admin bootstrap."""
        return await self.find_all_by(role=role, deleted_at=None, **filters)
