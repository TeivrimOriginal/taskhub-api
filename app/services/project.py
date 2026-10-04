"""Project service: ownership rules, counters and cache invalidation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import invalidate_pattern, make_key
from app.core.errors import PermissionDeniedError, ProjectNotFoundError
from app.core.logging import get_logger
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
        search: str | None = None,
        is_active: bool | None = None,
    ) -> tuple[list[Project], int]:
        """List projects visible to `user`.

        Staff see every project; everyone else sees only their own. This single branch
        is the reason no endpoint has to re-implement the visibility rule.
        """
        stmt = self.projects.make_listing(
            owner_id=None if user.is_staff else user.id,
            search=search,
            is_active=is_active,
        )
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
        await self.session.refresh(project)
        await self._invalidate(owner.id)
        logger.info("project_created", project_id=str(project.id), owner=str(owner.id))
        return project

    # ------------------------------------------------------------------ update
    async def update(
        self, project_id: uuid.UUID, payload: ProjectUpdate, *, actor: User
    ) -> Project:
        """Update a project; only the owner or an admin may do so."""
        project = await self.get(project_id)
        self._assert_may_manage(project, actor)
        values = payload.model_dump(exclude_unset=True)
        if values:
            project = await self.projects.update(project, values)
            project.last_activity_at = datetime.now(UTC)
            await self.session.commit()
            await self._invalidate(project.owner_id)
            logger.info("project_updated", project_id=str(project.id), fields=sorted(values))
        return project

    # ------------------------------------------------------------------ delete
    async def delete(self, project_id: uuid.UUID, *, actor: User) -> None:
        """Soft-delete the project and its tasks (children of a soft-deleted parent
        would otherwise stay visible through `GET /tasks`)."""
        project = await self.get(project_id)
        self._assert_may_manage(project, actor)

        now = datetime.now(UTC)
        project.deleted_at = now
        project.is_active = False
        for task in await self.tasks.find_all_by(project_id=project_id, deleted_at=None):
            task.deleted_at = now

        await self.projects.insert(project, flush=False)
        await self.session.commit()
        await self._invalidate(project.owner_id)
        logger.info(
            "project_deleted",
            project_id=str(project.id),
            tasks_soft_deleted=True,
            actor=str(actor.id),
        )

    # ----------------------------------------------------------------- helpers
    @staticmethod
    def _assert_may_manage(project: Project, actor: User) -> None:
        if project.owner_id != actor.id and not actor.is_superuser and actor.role.value != "admin":
            raise PermissionDeniedError("Only the project owner or an admin may change it")

    @staticmethod
    async def _invalidate(owner_id: uuid.UUID) -> None:
        await invalidate_pattern(f"{make_key('project')}*")
        await invalidate_pattern(f"{make_key('stats')}*")
        logger.debug("project_cache_invalidated", owner_id=str(owner_id))
