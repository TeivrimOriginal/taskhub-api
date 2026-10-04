"""Declarative base and constraint naming convention.

The naming convention is what makes Alembic autogenerate stable and what keeps
migrations readable: every constraint gets an explicit, deterministic name instead of
`users_email_key` picked by PostgreSQL.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION: dict[str, Any] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    """Timezone-aware "now".

    Python-side defaults (instead of only `server_default=func.now()`) matter for more
    than style: a column whose value comes back only from the server is marked *expired*
    after flush, and reading it afterwards triggers lazy IO — which raises
    `MissingGreenlet` under async SQLAlchemy. Keeping the value in Python leaves the
    identity map always readable; `server_default` stays for the DDL so plain SQL
    inserts still get a timestamp.
    """
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Common declarative base for every ORM model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def __repr__(self) -> str:
        identifier = getattr(self, "id", None)
        return f"<{type(self).__name__} id={identifier}>"


class TimestampMixin:
    """`created_at` / `updated_at` maintained by the ORM and by the database."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    """Logical deletion: rows stay for audit but disappear from normal queries."""

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=None,
        nullable=True,
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def mark_deleted(self) -> None:
        from datetime import UTC, datetime as dt

        self.deleted_at = dt.now(UTC)
