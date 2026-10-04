"""User management service (admin/moderator operations)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import invalidate, make_key
from app.core.constants import Role
from app.core.errors import PermissionDeniedError, UserAlreadyExistsError, UserNotFoundError
from app.core.logging import get_logger
from app.core.security import hash_password
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.user import UserCreate, UserRead, UserUpdate

logger = get_logger(__name__)


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
        search: str | None = None,
        role: Role | None = None,
        is_active: bool | None = None,
        include_deleted: bool = False,
    ) -> tuple[list[User], int]:
        """Paginated listing for staff members."""
        stmt = self.users.make_listing(
            search=search,
            role=role,
            is_active=is_active,
            include_deleted=include_deleted,
        )
        return await self.users.paginate(stmt, page=page, size=size)

    # ------------------------------------------------------------------ create
    async def create(
        self, payload: UserCreate, *, role: Role = Role.USER, actor: User | None = None
    ) -> User:
        """Staff-only account creation (registration goes through `AuthService`)."""
        email = payload.email.strip().lower()
        username = payload.username.strip().lower()
        if await self.users.email_taken(email) or await self.users.username_taken(username):
            raise UserAlreadyExistsError

        user = User(
            email=email,
            username=username,
            full_name=payload.full_name,
            role=role,
            hashed_password=hash_password(payload.password, rounds=12),
        )
        if actor is not None:
            user.is_active = True
        await self.users.insert(user)
        await self.session.commit()
        await self.session.refresh(user)
        logger.info(
            "user_created_by_staff", user_id=str(user.id), actor=str(actor.id if actor else None)
        )
        return user

    # ------------------------------------------------------------------ update
    async def update(self, user_id: uuid.UUID, payload: UserUpdate, *, actor: User) -> User:
        """Update an account, enforcing the permission rules first."""
        target = await self.get(user_id)
        self._assert_may_manage(actor, target)

        values = payload.model_dump(exclude_unset=True, exclude_none=True)
        if "email" in values:
            new_email = str(values["email"]).strip().lower()
            if await self.users.email_taken(new_email, exclude_id=user_id):
                raise UserAlreadyExistsError("A user with this e-mail already exists")
            values["email"] = new_email
        if "username" in values:
            new_username = str(values["username"]).strip().lower()
            if await self.users.username_taken(new_username, exclude_id=user_id):
                raise UserAlreadyExistsError("A user with this username already exists")
            values["username"] = new_username

        # Only an admin may change the privileged flags.
        if not actor.is_superuser and actor.role is not Role.ADMIN:
            values.pop("is_active", None)
            values.pop("role", None)

        user = await self.users.update(target, values)
        await self.session.commit()
        await self._invalidate(target)
        logger.info(
            "user_updated", user_id=str(user.id), actor=str(actor.id), fields=sorted(values)
        )
        return user

    # ------------------------------------------------------------------ delete
    async def deactivate(self, user_id: uuid.UUID, *, actor: User) -> None:
        """Soft-delete an account; the rows stay for audit."""
        target = await self.get(user_id)
        self._assert_may_manage(actor, target)
        target.deleted_at = datetime.now(UTC)
        target.is_active = False
        await self.users.insert(target, flush=False)
        await self.session.commit()
        await self._invalidate(target)
        logger.info("user_deactivated", user_id=str(user_id), actor=str(actor.id))

    async def reactivate(self, user_id: uuid.UUID, *, actor: User) -> User:
        """Restore a soft-deleted account."""
        target = await self.users.get(user_id)
        if target is None:
            raise UserNotFoundError(details={"user_id": str(user_id)})
        self._assert_may_manage(actor, target)
        target.deleted_at = None
        target.is_active = True
        await self.users.insert(target, flush=False)
        await self.session.commit()
        await self._invalidate(target)
        logger.info("user_reactivated", user_id=str(user_id), actor=str(actor.id))
        return target

    # ----------------------------------------------------------------- helpers
    @staticmethod
    def _assert_may_manage(actor: User, target: User) -> None:
        if not actor.can_manage(target):
            raise PermissionDeniedError("You may only manage your own account")

    @staticmethod
    async def _invalidate(user: User) -> None:
        await invalidate(make_key("user", user.id), make_key("stats", user.id))

    def to_read(self, user: User) -> UserRead:
        """Explicit ORM → schema conversion (keeps `PasswordOut` impossible to leak)."""
        return UserRead.model_validate(user, from_attributes=True)
