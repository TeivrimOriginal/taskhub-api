"""Generic CRUD repository shared by every entity."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base


def rowcount(result: object) -> int:
    """Row count of an `UPDATE`/`DELETE`, tolerating a driver that does not report it."""
    return int(getattr(result, "rowcount", 0) or 0)


class BaseRepository[ModelT: Base]:
    """CRUD primitives for one model.

    The constructor takes the session, not the other way round: repositories are created
    per request by the service layer, so one request can never accidentally reuse
    another request's session.
    """

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ create
    async def insert(self, entity: ModelT, *, flush: bool = True) -> ModelT:
        """Add an entity to the unit of work.

        `flush` and never `commit`: the transaction belongs to the service layer, which
        may perform several writes before deciding whether they succeed together.
        """
        self.session.add(entity)
        if flush:
            await self.session.flush()
        return entity

    async def insert_many(self, entities: Sequence[ModelT]) -> None:
        """Add several entities in one flush."""
        self.session.add_all(list(entities))
        await self.session.flush()

    # -------------------------------------------------------------------- read
    async def get(self, entity_id: uuid.UUID) -> ModelT | None:
        """Fetch by primary key, or `None`."""
        return await self.session.get(self.model, entity_id)

    async def find_one_by(self, **filters: Any) -> ModelT | None:
        """First row matching equality filters, or `None`."""
        return await self._scalar(select(self.model).filter_by(**filters).limit(1))

    async def find_all_by(self, **filters: Any) -> list[ModelT]:
        """Every row matching equality filters."""
        return await self._scalars(select(self.model).filter_by(**filters))

    async def exists(self, **filters: Any) -> bool:
        """`True` if at least one row matches — cheaper than loading an entity."""
        stmt = select(func.count()).select_from(self.model).filter_by(**filters)
        result = await self.session.execute(stmt)
        return bool(result.scalar_one())

    async def count(self, **filters: Any) -> int:
        """Number of rows matching equality filters."""
        stmt = select(func.count()).select_from(self.model).filter_by(**filters)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def paginate(
        self,
        stmt: Select[Any],
        *,
        page: int,
        size: int,
    ) -> tuple[list[ModelT], int]:
        """Apply `LIMIT/OFFSET` to a `SELECT` and return `(items, total)`.

        The total comes from `COUNT(*) OVER ()`, so the page and its total cost one
        round trip instead of two. The statement keeps its `ORDER BY` (only the implicit
        ordering added by `paginate()` itself would be ambiguous).
        """
        total_window = func.count().over().label("total")
        paged = stmt.add_columns(total_window).limit(size).offset((page - 1) * size)
        rows = (await self.session.execute(paged)).unique().all()
        if not rows:
            return [], 0
        return cast(list[ModelT], [row[0] for row in rows]), int(rows[0][-1])

    # ------------------------------------------------------------------ update
    async def update(self, entity: ModelT, values: dict[str, Any]) -> ModelT:
        """Apply a partial update and flush."""
        for field, value in values.items():
            setattr(entity, field, value)
        await self.session.flush()
        return entity

    async def delete(self, entity: ModelT) -> None:
        """Hard delete; the ORM cascades to children where the FK says so."""
        await self.session.delete(entity)
        await self.session.flush()

    # ----------------------------------------------------------------- helpers
    def make_select(self) -> Select[Any]:
        """A bare `SELECT * FROM <table>` for callers that need to add filters."""
        return select(self.model)

    async def _scalar(self, stmt: Select[Any]) -> ModelT | None:
        result = await self.session.execute(stmt)
        return cast(ModelT | None, result.unique().scalars().first())

    async def _scalars(self, stmt: Select[Any]) -> list[ModelT]:
        result = await self.session.execute(stmt)
        return cast(list[ModelT], list(result.unique().scalars().all()))
