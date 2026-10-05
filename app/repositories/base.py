"""Generic CRUD repository — the same six actions for every table.

Why a base class
----------------
Seven tables need `get`, `find`, `insert`, `update`, `delete`, `count` and `paginate`.
Writing them seven times is how the seven copies start to differ. So they are written
once here, and every repository only adds the queries that are *specific* to its table
(``make_listing``, ``bulk_overdue``, ``recalculate_counters`` …).

How the layers fit
------------------
    router  ->  service  ->  repository  ->  SQL
    HTTP       rules      queries        text

A repository never decides *whether* an action is allowed; that is the service's job. It
only knows how to ask the database for rows and how to give them back.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base


def rowcount(result: object) -> int:
    """How many rows an `UPDATE` or `DELETE` touched.

    Not every driver reports a count, so a missing value becomes 0 instead of `None`.
    """
    return int(getattr(result, "rowcount", 0) or 0)


class BaseRepository[ModelT: Base]:
    """CRUD primitives for one model.

    The constructor takes the session, not the other way round. A repository is built per
    request by the service layer, so one request can never reuse another request's
    session — the most common way to get very strange bugs in async code.
    """

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ create
    async def insert(self, entity: ModelT, *, flush: bool = True) -> ModelT:
        """Add a row and send it to the database.

        `flush` and never `commit`. A *flush* sends the SQL but keeps the transaction
        open; a *commit* closes it. The service layer decides when the transaction ends,
        because it may write several rows that must all succeed together — or all fail.
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

        A client asks for "page 3, size 20". The database has to answer two questions:
        *which 20 rows* and *how many rows are there in total*. The obvious way is two
        queries. This is one: `COUNT(*) OVER ()` adds the total as an extra column to
        every row, so the page and its count arrive together.

        The statement keeps the `ORDER BY` it was given — order must be decided before
        the rows are cut, or page 3 and page 4 could show the same row.
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
