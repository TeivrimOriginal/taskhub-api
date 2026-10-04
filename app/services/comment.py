"""Comment service."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import invalidate_pattern, make_key
from app.core.constants import ActivityAction
from app.core.errors import (
    CommentNotFoundError,
    PermissionDeniedError,
    TaskNotFoundError,
)
from app.core.logging import get_logger
from app.models.activity_log import ActivityLog
from app.models.comment import Comment
from app.models.project import Project
from app.models.task import Task
from app.models.user import User
from app.repositories.comment import CommentRepository
from app.repositories.project import ProjectRepository
from app.repositories.task import TaskRepository

logger = get_logger(__name__)


class CommentService:
    """Business rules around task comments."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.comments = CommentRepository(session)
        self.tasks = TaskRepository(session)
        self.projects = ProjectRepository(session)

    # -------------------------------------------------------------------- read
    async def get(self, comment_id: uuid.UUID) -> Comment:
        """Fetch a live comment or raise."""
        comment = await self.comments.get_alive(comment_id)
        if comment is None:
            raise CommentNotFoundError(details={"comment_id": str(comment_id)})
        return comment

    async def list_for_task(
        self,
        *,
        task_id: uuid.UUID,
        page: int,
        size: int,
        actor: User,
    ) -> tuple[list[Comment], int]:
        """Comments of a task, after the read permission has been checked."""
        task = await self._task_with_permission(task_id, actor)
        stmt = self.comments.make_listing(task_id=task.id)
        return await self.comments.paginate(stmt, page=page, size=size)

    # ------------------------------------------------------------------ create
    async def create(self, *, task_id: uuid.UUID, body: str, actor: User) -> Comment:
        """Add a comment and bump the project's activity timestamp."""
        task = await self._task_with_permission(task_id, actor)
        project = await self.projects.get(task.project_id)

        comment = Comment(task_id=task.id, author_id=actor.id, body=body.strip())
        await self.comments.insert(comment)

        if project is not None:
            project.last_activity_at = datetime.now(UTC)

        # SAVEPOINT: the audit row must not be able to poison the comment transaction.
        try:
            async with self.session.begin_nested():
                self.session.add(
                    ActivityLog(
                        user_id=actor.id,
                        task_id=task.id,
                        action=ActivityAction.COMMENT_CREATED.value,
                        entity_type="comment",
                        entity_id=comment.id,
                        payload={"comment_id": str(comment.id), "excerpt": comment.body[:120]},
                    )
                )
                await self.session.flush()
        except Exception:
            logger.warning("activity_log_write_failed", comment_id=str(comment.id), exc_info=True)

        await self.session.commit()
        await self.session.refresh(comment)
        await self._invalidate()
        logger.info("comment_created", comment_id=str(comment.id), task_id=str(task.id))
        return comment

    # ------------------------------------------------------------------ update
    async def update(self, comment_id: uuid.UUID, *, body: str, actor: User) -> Comment:
        """Edit a comment — only its author or a staff member may do so."""
        comment = await self.get(comment_id)
        if comment.author_id != actor.id and not actor.is_staff:
            raise PermissionDeniedError("Only the author may edit a comment")

        comment.body = body.strip()
        await self.comments.insert(comment, flush=False)
        await self.session.commit()
        await self._invalidate()
        logger.info("comment_updated", comment_id=str(comment_id), actor=str(actor.id))
        return comment

    # ------------------------------------------------------------------ delete
    async def delete(self, comment_id: uuid.UUID, *, actor: User) -> None:
        """Soft-delete a comment."""
        comment = await self.get(comment_id)
        if comment.author_id != actor.id and not actor.is_staff:
            raise PermissionDeniedError("Only the author may delete a comment")

        comment.deleted_at = datetime.now(UTC)
        await self.comments.insert(comment, flush=False)
        await self.session.commit()
        await self._invalidate()
        logger.info("comment_deleted", comment_id=str(comment_id), actor=str(actor.id))

    # ----------------------------------------------------------------- helpers
    async def _task_with_permission(self, task_id: uuid.UUID, actor: User) -> Task:
        """Load a task and assert the actor may read it.

        Staff see everything; everyone else must own the project or be the assignee.
        """
        task = await self.tasks.get_alive(task_id)
        if task is None:
            raise TaskNotFoundError(details={"task_id": str(task_id)})
        if actor.is_staff or task.assignee_id == actor.id:
            return task

        project: Project | None = await self.projects.get_alive(task.project_id)
        if project is None or project.owner_id != actor.id:
            raise PermissionDeniedError("You may only read tasks in your own projects")
        return task

    @staticmethod
    async def _invalidate() -> None:
        await invalidate_pattern(f"{make_key('task')}*")
        await invalidate_pattern(f"{make_key('stats')}*")
