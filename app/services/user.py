"""User management service (admin/moderator operations).

Access rules come from `app.core.permissions`, cache groups from
`app.core.cache.invalidate_entities`. Password rules stay here, because they are specific to
accounts.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import invalidate_entities
from app.core.constants import CacheEntity, Role
from app.core.errors import UserAlreadyExistsError, UserNotFoundError
from app.core.logging import get_logger
from app.core.permissions import assert_manage_user, can_change_privileged_fields
from app.core.security import hash_password
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.user import UserCreate, UserUpdate

logger = get_logger(__name__)

#: Cost used when a staff member creates an account. Login uses the configured value.
STAFF_CREATE_BCRYPT_ROUNDS = 12

#: Fields that only an admin may touch on somebody else's account.
PRIVILEGED_FIELDS = ("is_active", "role")


class UserService:
    """CRUD and lifecycle rules for accounts."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)

    # -------------------------------------------------------------------- read
    async def get(self, user_id: uuid.UUID) -> User:
        """Fetch a live account or raise `UserNotFoundError`."""
        user = await self.users.get_alive(user_id)
        if user is None:
            raise UserNotFoundError(details={"user_id": str(user_id)})
        return user

    async def list(
        self,
        *,
        page: int,
        size: int,
        **filters: object,
    ) -> tuple[list[User], int]:
        """Paginated listing for staff members. Filters come from `FilterSet`."""
        stmt = self.users.make_listing(**filters)  # type: ignore[arg-type]
        return await self.users.paginate(stmt, page=page, size=size)

    # ------------------------------------------------------------------ create
    async def create(
        self,
        payload: UserCreate,
        *,
        role: Role = Role.USER,
        actor: User | None = None,
    ) -> User:
        """Create an account. Staff only — registration goes through `AuthService`."""
        email = payload.email.strip().lower()
        username = payload.username.strip().lower()
        if await self.users.email_taken(email) or await self.users.username_taken(username):
            raise UserAlreadyExistsError

        user = User(
            email=email,
            username=username,
            full_name=payload.full_name,
            role=role,
            hashed_password=hash_password(payload.password, rounds=STAFF_CREATE_BCRYPT_ROUNDS),
        )
        await self.users.insert(user)
        await self.session.commit()
        await invalidate_entities(CacheEntity.USER)
        logger.info(
            "user_created_by_staff", user_id=str(user.id), actor=str(actor.id if actor else None)
        )
        return user

    # ------------------------------------------------------------------ update
    async def update(self, user_id: uuid.UUID, payload: UserUpdate, *, actor: User) -> User:
        """Update an account, after the permission check.

        The rule is "you may manage yourself, an admin may manage anyone", and the same
        rule also decides which fields are allowed: only an admin may set `is_active` or
        `role`, so a user cannot promote themselves through `PATCH /auth/me`.
        """
        target = await self.get(user_id)
        assert_manage_user(actor, target)

        values = await self._clean_values(target, payload, actor=actor)
        if values:
            user = await self.users.update(target, values)
            await self.session.commit()
            await invalidate_entities(CacheEntity.USER)
            logger.info(
                "user_updated",
                user_id=str(user.id),
                actor=str(actor.id),
                fields=sorted(str(key) for key in values),
            )
        return target

    # ------------------------------------------------------------------ delete
    async def deactivate(self, user_id: uuid.UUID, *, actor: User) -> None:
        """Soft-delete an account. The row stays, so the audit history survives."""
        target = await self.get(user_id)
        assert_manage_user(actor, target)
        await self._set_deleted(target, deleted=True)
        logger.info("user_deactivated", user_id=str(user_id), actor=str(actor.id))

    async def reactivate(self, user_id: uuid.UUID, *, actor: User) -> User:
        """Restore a soft-deleted account."""
        target = await self.users.get(user_id)
        if target is None:
            raise UserNotFoundError(details={"user_id": str(user_id)})
        assert_manage_user(actor, target)
        await self._set_deleted(target, deleted=False)
        logger.info("user_reactivated", user_id=str(user_id), actor=str(actor.id))
        return target

    # ----------------------------------------------------------------- helpers
    async def _set_deleted(self, user: User, *, deleted: bool) -> None:
        """Flip the soft-delete flag and the matching `is_active` flag together.

        The two must never disagree: a row that is soft-deleted but still active would
        still pass the `is_active` filter in a listing.
        """
        user.deleted_at = datetime.now(UTC) if deleted else None
        user.is_active = not deleted
        await self.users.insert(user, flush=False)
        await self.session.commit()
        await invalidate_entities(CacheEntity.USER)

    async def _clean_values(
        self,
        target: User,
        payload: UserUpdate,
        *,
        actor: User,
    ) -> dict[str, object]:
        """Work out which fields may change, and check the unique ones.

        Returns only the fields that may actually change, so a caller passing a field it
        is not allowed to change gets a silent no-op instead of a confusing partial write.
        """
        values: dict[str, object] = payload.model_dump(exclude_unset=True, exclude_none=True)

        if not can_change_privileged_fields(actor):
            for field in PRIVILEGED_FIELDS:
                values.pop(field, None)

        if "email" in values:
            new_email = str(values["email"]).strip().lower()
            if await self.users.email_taken(new_email, exclude_id=target.id):
                raise UserAlreadyExistsError("A user with this e-mail already exists")
            values["email"] = new_email

        if "username" in values:
            new_username = str(values["username"]).strip().lower()
            if await self.users.username_taken(new_username, exclude_id=target.id):
                raise UserAlreadyExistsError("A user with this username already exists")
            values["username"] = new_username

        return values
