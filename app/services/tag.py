"""Tag service: normalisation, reuse and usage counters."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import invalidate_pattern, make_key
from app.core.errors import ConflictError, TagNotFoundError
from app.core.logging import get_logger
from app.models.tag import Tag
from app.models.user import User
from app.repositories.tag import TagRepository
from app.schemas.tag import TagCreate

logger = get_logger(__name__)


class TagService:
    """Business rules around the shared tag catalogue."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.tags = TagRepository(session)

    async def get(self, tag_id: uuid.UUID) -> Tag:
        """Fetch a tag or raise."""
        tag = await self.tags.get(tag_id)
        if tag is None:
            raise TagNotFoundError(details={"tag_id": str(tag_id)})
        return tag

    async def list_tags(
        self, *, page: int, size: int, search: str | None = None
    ) -> tuple[list[Tag], int]:
        """Paginated tag catalogue (not `list`, which would shadow the builtin)."""
        return await self.tags.paginate(
            self.tags.make_listing(search=search),
            page=page,
            size=size,
        )

    async def get_or_create(self, name: str, *, color: str, creator: User | None = None) -> Tag:
        """Return the existing tag with this name or create it.

        Idempotent by design: attaching the same label twice must never fail.
        """
        normalised = name.strip().lower()
        existing = await self.tags.find_one_by(name=normalised)
        if existing is not None:
            return existing

        tag = Tag(name=normalised, color=color, created_by=creator.id if creator else None)
        await self.tags.insert(tag)
        await self.session.commit()
        await self.session.refresh(tag)
        await self._invalidate()
        logger.info("tag_created", tag=tag.name, tag_id=str(tag.id))
        return tag

    async def create(self, payload: TagCreate, *, creator: User) -> Tag:
        """Create a tag explicitly, reusing an existing identical name."""
        return await self.get_or_create(payload.name, color=payload.color, creator=creator)

    async def update(self, tag_id: uuid.UUID, *, values: dict[str, str], actor: User) -> Tag:
        """Rename or recolour a tag (staff only — enforced by the route)."""
        tag = await self.get(tag_id)
        if "name" in values and values["name"] != tag.name:
            normalised = values["name"].strip().lower()
            clash = await self.tags.find_one_by(name=normalised)
            if clash is not None and clash.id != tag_id:
                raise ConflictError(
                    "A tag with this name already exists",
                    details={"tag_id": str(tag_id)},
                )
            values["name"] = normalised

        tag = await self.tags.update(tag, values)
        await self.session.commit()
        await self._invalidate()
        logger.info("tag_updated", tag_id=str(tag_id), actor=str(actor.id), fields=sorted(values))
        return tag

    async def delete(self, tag_id: uuid.UUID, *, actor: User) -> None:
        """Remove a tag from the catalogue; associations cascade in the database."""
        tag = await self.get(tag_id)
        await self.tags.delete(tag)
        await self.session.commit()
        await self._invalidate()
        logger.info("tag_deleted", tag_id=str(tag_id), actor=str(actor.id))

    async def most_used(self, *, limit: int = 10) -> list[tuple[Tag, int]]:
        """Tags ordered by usage — feeds the "popular labels" widget."""
        return await self.tags.most_used(limit=limit)

    @staticmethod
    async def _invalidate() -> None:
        await invalidate_pattern(f"{make_key('task')}*")
        await invalidate_pattern(f"{make_key('tag')}*")
