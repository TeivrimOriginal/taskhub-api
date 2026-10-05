"""Tag service: normalisation, reuse and usage counters.

Cache invalidation is delegated to `app.core.cache.invalidate_entities`, so this file has
no idea what a cache key looks like.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import invalidate_entities
from app.core.constants import CacheEntity
from app.core.errors import ConflictError, TagNotFoundError
from app.core.logging import get_logger
from app.models.tag import Tag
from app.models.user import User
from app.repositories.tag import TagRepository
from app.schemas.tag import TagCreate

logger = get_logger(__name__)

#: How many tags "most used" returns when the caller does not say.
DEFAULT_POPULAR_LIMIT = 10


class TagService:
    """Business rules around the shared tag catalogue."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.tags = TagRepository(session)

    # -------------------------------------------------------------------- read
    async def get(self, tag_id: uuid.UUID) -> Tag:
        """Fetch a tag or raise `TagNotFoundError`."""
        tag = await self.tags.get(tag_id)
        if tag is None:
            raise TagNotFoundError(details={"tag_id": str(tag_id)})
        return tag

    async def list_tags(
        self,
        *,
        page: int,
        size: int,
        **filters: object,
    ) -> tuple[list[Tag], int]:
        """Paginated tag catalogue.

        Named `list_tags` rather than `list` so it cannot shadow the builtin inside this
        class. Filters arrive from `FilterSet.to_repository()`, like every other list.
        """
        stmt = self.tags.make_listing(**filters)  # type: ignore[arg-type]
        return await self.tags.paginate(stmt, page=page, size=size)

    async def most_used(self, *, limit: int = DEFAULT_POPULAR_LIMIT) -> list[tuple[Tag, int]]:
        """Tags ordered by how many live tasks carry them."""
        return await self.tags.most_used(limit=limit)

    # ------------------------------------------------------------------ create
    async def get_or_create(self, name: str, *, color: str, creator: User | None = None) -> Tag:
        """Return the tag with this name, or create it.

        Idempotent on purpose: attaching the same label twice must never fail, so this is
        the path the task endpoints use.
        """
        normalised = name.strip().lower()
        existing = await self.tags.find_one_by(name=normalised)
        if existing is not None:
            return existing

        tag = Tag(name=normalised, color=color, created_by=creator.id if creator else None)
        await self.tags.insert(tag)
        await self.session.commit()
        await invalidate_entities(CacheEntity.TAG)
        logger.info("tag_created", tag=tag.name, tag_id=str(tag.id))
        return tag

    async def create(self, payload: TagCreate, *, creator: User) -> Tag:
        """Create a tag explicitly, reusing an existing identical name."""
        return await self.get_or_create(payload.name, color=payload.color, creator=creator)

    # ------------------------------------------------------------------ update
    async def update(self, tag_id: uuid.UUID, *, values: dict[str, str], actor: User) -> Tag:
        """Rename or recolour a tag. Only staff reach this (the route enforces it)."""
        tag = await self.get(tag_id)
        values = await self._normalise_for_update(tag, values)

        tag = await self.tags.update(tag, values)
        await self.session.commit()
        await invalidate_entities(CacheEntity.TAG)
        self._log_change(tag_id, actor, values)
        return tag

    # ------------------------------------------------------------------ delete
    async def delete(self, tag_id: uuid.UUID, *, actor: User) -> None:
        """Remove a tag from the catalogue; task links disappear in the database."""
        tag = await self.get(tag_id)
        await self.tags.delete(tag)
        await self.session.commit()
        await invalidate_entities(CacheEntity.TAG)
        logger.info("tag_deleted", tag_id=str(tag_id), actor=str(actor.id))

    # ----------------------------------------------------------------- helpers
    async def _normalise_for_update(self, tag: Tag, values: dict[str, str]) -> dict[str, str]:
        """Lower-case a new name and refuse it if another tag already uses it."""
        new_name = values.get("name")
        if new_name is None or new_name == tag.name:
            return values

        normalised = new_name.strip().lower()
        clash = await self.tags.find_one_by(name=normalised)
        if clash is not None and clash.id != tag.id:
            raise ConflictError(
                "A tag with this name already exists",
                details={"tag_id": str(tag.id)},
            )
        return {**values, "name": normalised}

    def _log_change(self, tag_id: uuid.UUID, actor: User, values: dict[str, str]) -> None:
        """One log line for a write, so every service logs the same way."""
        logger.info(
            "tag_updated",
            tag_id=str(tag_id),
            actor=str(actor.id),
            fields=sorted(str(key) for key in values),
        )
