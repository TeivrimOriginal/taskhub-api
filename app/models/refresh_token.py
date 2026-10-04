"""Refresh token records — the only server-side state in the auth flow.

Storing tokens instead of trusting pure JWTs buys three things: revocation, rotation
and theft detection. Only a SHA-256 fingerprint is stored, so a database leak does not
hand out valid sessions.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utcnow

if TYPE_CHECKING:
    from app.models.user import User


class RefreshToken(Base):
    """One issued refresh token, part of a login *family*.

    Flow: login creates family F with token T1. Every refresh marks the presented token
    as used and issues a new token inside the same family. If an already-used token is
    presented again, the whole family F is revoked — the signature of a stolen token.
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_user_id", "user_id"),
        Index("ix_refresh_tokens_family_id", "family_id"),
        Index("ix_refresh_tokens_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    jti: Mapped[str] = mapped_column(String(length=64), unique=True, nullable=False, index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(default=uuid.uuid4, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(length=64), nullable=False)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    user_agent: Mapped[str | None] = mapped_column(String(length=256), default=None)
    ip_address: Mapped[str | None] = mapped_column(INET, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped[User] = relationship(back_populates="refresh_tokens")

    # ------------------------------------------------------------------ helpers
    @property
    def is_active(self) -> bool:
        """Usable right now: not used, not revoked, not expired."""
        from datetime import UTC, datetime as dt

        now = dt.now(UTC)
        return self.used_at is None and self.revoked_at is None and self.expires_at > now

    @property
    def was_used(self) -> bool:
        return self.used_at is not None
