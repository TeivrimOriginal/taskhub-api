"""Comment service.

Access rules come from `app.core.permissions` and cache groups from
`app.core.cache.invalidate_entities`, so this file only holds comment business logic.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import invalidate_entities
from app.core.constants import ActivityAction, CacheEntity
from app.core.errors import CommentNotFoundError, TaskNotFoundError
from app.core.logging import get_logger
from app.core.permissions import assert_delete_comment, assert_edit_comment, assert_read_task
from app.models.activity_log import ActivityLog
from app.models.comment import Comment
from app.models.task import Task
from app.models.user import User
from app.repositories.comment import CommentRepository
from app.repositories.project import ProjectRepository
from app.repositories.task import TaskRepository

logger = get_logger(__name__)

#: How many characters of a comment are copied into the audit log.
EXCERPT_LENGTH = 120


class CommentService:
    """Business rules around task comments."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.comments = CommentRepository(session)
        self.tasks = TaskRepository(session)
        self.projects = ProjectRepository(session)

    # -------------------------------------------------------------------- read
    async def get(self, comment_id: uuid.UUID) -> Comment:
        """Fetch a live comment or raise `CommentNotFoundError`."""
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
        """Comments of one task, newest first.

        The read check runs first: knowing a task id must not be enough to read its
        discussion.
        """
        task = await self._readable_task(task_id, actor)
        stmt = self.comments.make_listing(task_id=task.id)
        return await self.comments.paginate(stmt, page=page, size=size)

    # ------------------------------------------------------------------ create
    async def create(self, *, task_id: uuid.UUID, body: str, actor: User) -> Comment:
        """Add a comment and record it in the audit log, in one transaction."""
        task = await self._readable_task(task_id, actor)
        project = await self.projects.get(task.project_id)

        comment = Comment(task_id=task.id, author_id=actor.id, body=body.strip())
        await self.comments.insert(comment)

        if project is not None:
            project.last_activity_at = datetime.now(UTC)

        await self._write_audit_row(comment, task, actor)

        await self.session.commit()
        await invalidate_entities(CacheEntity.COMMENT)
        logger.info("comment_created", comment_id=str(comment.id), task_id=str(task.id))
        return comment

    # ------------------------------------------------------------------ update
    async def update(self, comment_id: uuid.UUID, *, body: str, actor: User) -> Comment:
        """Edit a comment. Only its author or a staff member may do so."""
        comment = await self.get(comment_id)
        assert_edit_comment(actor, comment)

        comment.body = body.strip()
        await self.comments.insert(comment, flush=False)
        await self.session.commit()
        await invalidate_entities(CacheEntity.COMMENT)
        logger.info("comment_updated", comment_id=str(comment_id), actor=str(actor.id))
        return comment

    # ------------------------------------------------------------------ delete
    async def delete(self, comment_id: uuid.UUID, *, actor: User) -> None:
        """Soft-delete a comment, so the audit history stays readable."""
        comment = await self.get(comment_id)
        assert_delete_comment(actor, comment)

        comment.deleted_at = datetime.now(UTC)
        await self.comments.insert(comment, flush=False)
        await self.session.commit()
        await invalidate_entities(CacheEntity.COMMENT)
        logger.info("comment_deleted", comment_id=str(comment_id), actor=str(actor.id))

    # ----------------------------------------------------------------- helpers
    async def _readable_task(self, task_id: uuid.UUID, actor: User) -> Task:
        """Load a task and ask `permissions` whether the actor may read it.

        Raises `TaskNotFoundError` when the row is gone and `PermissionDeniedError` when
        the actor has no business seeing it — both decided in `app.core.permissions`, so
        the comment endpoints and the task endpoints cannot disagree.
        """
        task = await self.tasks.get_alive(task_id)
        if task is None:
            raise TaskNotFoundError(details={"task_id": str(task_id)})

        project = await self.projects.get_alive(task.project_id)
        assert_read_task(actor, project, task)
        return task

    async def _write_audit_row(self, comment: Comment, task: Task, actor: User) -> None:
        """Append one audit row, inside a SAVEPOINT.

        The savepoint is the point: a broken audit row must not be able to roll back the
        comment it is describing, and it must not leave the session unusable (a failed
        flush poisons the session until an explicit rollback).
        """
        try:
            async with self.session.begin_nested():
                self.session.add(
                    ActivityLog(
                        user_id=actor.id,
                        task_id=task.id,
                        action=ActivityAction.COMMENT_CREATED.value,
                        entity_type="comment",
                        entity_id=comment.id,
                        payload={
                            "comment_id": str(comment.id),
                            "excerpt": comment.body[:EXCERPT_LENGTH],
                        },
                    )
                )
                await self.session.flush()
        except Exception:
            logger.warning("activity_log_write_failed", comment_id=str(comment.id), exc_info=True)
