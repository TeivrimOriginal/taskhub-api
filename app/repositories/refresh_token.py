"""Refresh token queries."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.refresh_token import RefreshToken
from app.repositories.base import BaseRepository, rowcount


class RefreshTokenRepository(BaseRepository[RefreshToken]):
    """Access to issued refresh tokens — the only mutable auth state."""

    model = RefreshToken

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_by_jti(self, jti: str) -> RefreshToken | None:
        """Look a token up by its unique `jti` claim."""
        return await self.find_one_by(jti=jti)

    async def get_live_for_family(self, family_id: uuid.UUID) -> RefreshToken | None:
        """Newest still-usable token of a family, if any."""
        stmt = (
            select(RefreshToken)
            .where(
                RefreshToken.family_id == family_id,
                RefreshToken.used_at.is_(None),
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > datetime.now(UTC),
            )
            .order_by(RefreshToken.created_at.desc())
            .limit(1)
        )
        return await self._scalar(stmt)

    async def revoke_family(self, family_id: uuid.UUID) -> int:
        """Revoke every token of a family — the response to detected token reuse."""
        result = await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await self.session.flush()
        return rowcount(result)

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
        """Revoke every active session of a user (logout-all / password change)."""
        result = await self.session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.used_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        await self.session.flush()
        return rowcount(result)

    async def count_active_for_user(self, user_id: uuid.UUID) -> int:
        """How many sessions the account currently has."""
        result = await self.session.execute(
            select(func.count())
            .select_from(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.used_at.is_(None),
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > datetime.now(UTC),
            )
        )
        return int(result.scalar_one())

    async def purge_expired(self, *, older_than_days: int = 30) -> int:
        """Delete tokens that expired more than `older_than_days` ago.

        Called by the nightly Celery job; expired rows serve no purpose once the
        token they back can no longer authenticate anything.
        """
        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        result = await self.session.execute(
            delete(RefreshToken).where(RefreshToken.expires_at < cutoff)
        )
        await self.session.flush()
        return rowcount(result)
