"""Project service: ownership rules, counters and cache invalidation.

This service owns nothing about *who* may do something. Every access question is asked of
`app.core.permissions`, so the rule for a project, a task and a comment cannot drift apart,
and every cache question is asked of `app.core.cache.invalidate_entities`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import invalidate_entities
from app.core.constants import CacheEntity
from app.core.errors import ProjectNotFoundError
from app.core.logging import get_logger
from app.core.permissions import assert_write_project, listing_owner_id
from app.models.project import Project
from app.models.user import User
from app.repositories.project import ProjectRepository
from app.repositories.task import TaskRepository
from app.schemas.project import ProjectCreate, ProjectUpdate

logger = get_logger(__name__)


class ProjectService:
    """Business rules around projects."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.projects = ProjectRepository(session)
        self.tasks = TaskRepository(session)

    # -------------------------------------------------------------------- read
    async def get(self, project_id: uuid.UUID) -> Project:
        """Fetch a live project or raise `ProjectNotFoundError`."""
        project = await self.projects.get_alive(project_id)
        if project is None:
            raise ProjectNotFoundError(details={"project_id": str(project_id)})
        return project

    async def list_for_user(
        self,
        *,
        user: User,
        page: int,
        size: int,
        **filters: object,
    ) -> tuple[list[Project], int]:
        """List the projects `user` may see.

        The filters arrive as keywords straight from `FilterSet.to_repository()`, so a new
        filter needs no change here. `listing_owner_id()` applies the visibility rule:
        staff get every project, everyone else gets only their own.
        """
        stmt = self.projects.make_listing(owner_id=listing_owner_id(user), **filters)  # type: ignore[arg-type]
        return await self.projects.paginate(stmt, page=page, size=size)

    # ------------------------------------------------------------------ create
    async def create(self, payload: ProjectCreate, *, owner: User) -> Project:
        """Create a project owned by the caller."""
        project = Project(
            owner_id=owner.id,
            name=payload.name,
            description=payload.description,
            color=payload.color,
            last_activity_at=datetime.now(UTC),
        )
        await self.projects.insert(project)
        await self.session.commit()
        await invalidate_entities(CacheEntity.PROJECT)
        logger.info("project_created", project_id=str(project.id), owner=str(owner.id))
        return project

    # ------------------------------------------------------------------ update
    async def update(
        self, project_id: uuid.UUID, payload: ProjectUpdate, *, actor: User
    ) -> Project:
        """Update a project. Only the owner or an admin may do it."""
        project = await self.get(project_id)
        assert_write_project(actor, project)

        values = payload.model_dump(exclude_unset=True)
        if values:
            project = await self.projects.update(project, values)
            project.last_activity_at = datetime.now(UTC)
            await self.session.commit()
            await invalidate_entities(CacheEntity.PROJECT)
            logger.info("project_updated", project_id=str(project.id), fields=sorted(values))
        return project

    # ------------------------------------------------------------------ delete
    async def delete(self, project_id: uuid.UUID, *, actor: User) -> None:
        """Soft-delete the project and its tasks.

        The tasks go with it, because a task whose project is gone would otherwise still be
        visible through `GET /tasks`. Soft, not hard: the audit history stays readable.
        """
        project = await self.get(project_id)
        assert_write_project(actor, project)

        now = datetime.now(UTC)
        project.deleted_at = now
        project.is_active = False
        for task in await self.tasks.find_all_by(project_id=project_id, deleted_at=None):
            task.deleted_at = now

        await self.projects.insert(project, flush=False)
        await self.session.commit()
        await invalidate_entities(CacheEntity.PROJECT, CacheEntity.TASK)
        logger.info(
            "project_deleted",
            project_id=str(project.id),
            tasks_soft_deleted=True,
            actor=str(actor.id),
        )
