"""Task service: status transitions, counters, tag limits, notifications."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from enum import Enum

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import invalidate_entities
from app.core.constants import (
    MAX_BULK_TAGS,
    QUEUE_NOTIFICATIONS,
    ActivityAction,
    CacheEntity,
    TaskStatus,
)
from app.core.errors import (
    TagLimitExceededError,
    TagNotFoundError,
    TaskNotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.permissions import assert_write_task, listing_owner_id
from app.models.activity_log import ActivityLog
from app.models.project import Project
from app.models.tag import Tag
from app.models.task import Task
from app.models.user import User
from app.repositories.project import ProjectRepository
from app.repositories.tag import TagRepository
from app.repositories.task import TaskRepository
from app.schemas.task import TaskCreate, TaskUpdate
from app.workers.dispatch import enqueue

logger = get_logger(__name__)

# Which status changes are worth an audit entry of their own.
_NOTABLE_TRANSITIONS = {TaskStatus.DONE, TaskStatus.CANCELLED, TaskStatus.IN_REVIEW}


def _jsonable(value: object) -> object:
    """Coerce a payload into something the JSONB column accepts.

    `uuid.UUID` and `datetime` are not JSON serialisable, and the failure would only show
    up at flush time — inside a transaction that already contains real work.
    """
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (uuid.UUID, datetime, date)):
        return str(value)
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class TaskService:
    """Business rules around tasks."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.tasks = TaskRepository(session)
        self.projects = ProjectRepository(session)
        self.tags = TagRepository(session)

    # -------------------------------------------------------------------- read
    async def get(self, task_id: uuid.UUID) -> Task:
        """Fetch a live task with its relations loaded, or raise."""
        task = await self.tasks.get_alive(task_id)
        if task is None:
            raise TaskNotFoundError(details={"task_id": str(task_id)})
        return task

    async def list_for_user(
        self,
        *,
        user: User,
        page: int,
        size: int,
        mine_only: bool = False,
        **filters: object,
    ) -> tuple[list[Task], int]:
        """List the tasks `user` may see.

        The filters arrive as keywords from `FilterSet.to_repository()`, so this method
        does not repeat their names. Two rules are applied here and nowhere else:

        * `listing_owner_id()` limits a normal user to their own projects; staff see all.
        * `?mine=true` replaces the assignee filter with the caller's own id, so a client
          cannot ask for another person's task list through that flag.
        """
        if mine_only:
            filters["assignee_id"] = user.id
        stmt = self.tasks.make_listing(
            owner_id=listing_owner_id(user),
            **filters,  # type: ignore[arg-type]
        )
        return await self.tasks.paginate(stmt, page=page, size=size)

    async def list_for_project(
        self,
        *,
        project: Project,
        page: int,
        size: int,
        **filters: object,
    ) -> tuple[list[Task], int]:
        """Tasks inside one project, after the visibility check has passed.

        Same mechanism as `list_for_user`; the project is fixed, so it is passed as a
        filter and cannot be overridden by the query string.
        """
        stmt = self.tasks.make_listing(project_id=project.id, **filters)  # type: ignore[arg-type]
        return await self.tasks.paginate(stmt, page=page, size=size)

    # ------------------------------------------------------------------ create
    async def create(self, payload: TaskCreate, *, actor: User) -> Task:
        """Create a task and enqueue the assignee notification."""
        project = await self.projects.get_alive(payload.project_id)
        if project is None:
            raise TaskNotFoundError(details={"project_id": str(payload.project_id)})
        # No `task` yet: this is the "create" rule, so only the project matters here.
        assert_write_task(actor, project)

        tags = await self._resolve_tags(payload.tag_ids)
        task = Task(
            project_id=project.id,
            assignee_id=payload.assignee_id,
            title=payload.title.strip(),
            description=payload.description,
            status=payload.status,
            priority=payload.priority,
            position=payload.position or await self.tasks.next_position(project.id),
            due_date=payload.due_date,
            estimate_minutes=payload.estimate_minutes,
            tags=tags,
        )
        if task.status is TaskStatus.IN_PROGRESS:
            task.started_at = datetime.now(UTC)
        if task.status.is_terminal:
            task.completed_at = datetime.now(UTC)

        await self.tasks.insert(task)
        project.last_activity_at = datetime.now(UTC)
        await self.log_activity(
            task, actor, ActivityAction.TASK_CREATED, project_id=str(project.id)
        )
        await self.projects.recalculate_counters(project.id)
        await self.session.commit()
        await invalidate_entities(CacheEntity.TASK)

        logger.info(
            "task_created",
            task_id=str(task.id),
            project_id=str(project.id),
            actor=str(actor.id),
            assignee=str(task.assignee_id),
        )
        await self._schedule_assignment_email(task)
        return await self._reload(task.id)

    # ------------------------------------------------------------------ update
    async def update(self, task_id: uuid.UUID, payload: TaskUpdate, *, actor: User) -> Task:
        """Partial update, including the status/priority side effects."""
        task = await self.get(task_id)
        project = await self._require_project(task.project_id)
        assert_write_task(actor, project, task)

        values = payload.model_dump(exclude_unset=True)
        if not values:
            return task

        previous_status = task.status
        previous_assignee = task.assignee_id
        now = datetime.now(UTC)

        if "status" in values and values["status"] is not None:
            values["status"] = self._apply_status_transition(task, values["status"], now=now)

        if values.get("assignee_id") is None and "assignee_id" in values:
            task.assignee_id = None

        task = await self.tasks.update(task, values)
        project.last_activity_at = now

        if previous_assignee != task.assignee_id and task.assignee_id is not None:
            await self.log_activity(task, actor, ActivityAction.TASK_ASSIGNED)
        if previous_status is not task.status:
            action = (
                ActivityAction.TASK_COMPLETED
                if task.status is TaskStatus.DONE
                else ActivityAction.TASK_UPDATED
            )
            await self.log_activity(task, actor, action, previous_status=previous_status.value)

        await self.projects.recalculate_counters(project.id)
        await self.session.commit()
        await invalidate_entities(CacheEntity.TASK)
        logger.info(
            "task_updated",
            task_id=str(task.id),
            actor=str(actor.id),
            fields=sorted(values),
            status=task.status.value,
        )
        return await self._reload(task.id)

    async def complete(self, task_id: uuid.UUID, *, actor: User) -> Task:
        """Shortcut for the most common transition."""
        return await self.update(task_id, TaskUpdate(status=TaskStatus.DONE), actor=actor)

    # ------------------------------------------------------------------ delete
    async def delete(self, task_id: uuid.UUID, *, actor: User) -> None:
        """Soft-delete a task so the history stays auditable."""
        task = await self.get(task_id)
        project = await self._require_project(task.project_id)
        assert_write_task(actor, project, task)

        task.deleted_at = datetime.now(UTC)
        await self.tasks.insert(task, flush=False)
        await self.log_activity(task, actor, ActivityAction.TASK_DELETED)
        await self.projects.recalculate_counters(project.id)
        await self.session.commit()
        await invalidate_entities(CacheEntity.TASK)
        logger.info("task_deleted", task_id=str(task_id), actor=str(actor.id))

    # --------------------------------------------------------------------- tags
    async def attach_tag(self, task_id: uuid.UUID, *, tag_id: uuid.UUID, actor: User) -> Task:
        """Attach an existing tag, enforcing the per-task limit."""
        task = await self.get(task_id)
        project = await self._require_project(task.project_id)
        assert_write_task(actor, project, task)

        tag = await self.tags.get(tag_id)
        if tag is None:
            raise TagNotFoundError(details={"tag_id": str(tag_id)})
        if len(task.tags) >= MAX_BULK_TAGS:
            raise TagLimitExceededError(details={"max_tags": MAX_BULK_TAGS})

        added = await self.tasks.attach_tag(task, tag)
        if added:
            await self.session.commit()
            await invalidate_entities(CacheEntity.TASK)
            logger.info("tag_attached", task_id=str(task_id), tag=str(tag.name))
        return await self._reload(task.id)

    async def detach_tag(self, task_id: uuid.UUID, *, tag_id: uuid.UUID, actor: User) -> Task:
        """Remove a tag from a task."""
        task = await self.get(task_id)
        project = await self._require_project(task.project_id)
        assert_write_task(actor, project, task)

        if await self.tasks.detach_tag(task, tag_id):
            await self.session.commit()
            await invalidate_entities(CacheEntity.TASK)
            logger.info("tag_detached", task_id=str(task_id), tag_id=str(tag_id))
        return await self._reload(task.id)

    # ----------------------------------------------------------------- helpers
    async def _require_project(self, project_id: uuid.UUID) -> Project:
        """Load a project or raise.

        `repositories.get()` returns `| None` because it is the honest signature for a
        generic lookup; every caller of a *known* project needs the invariant, so it is
        asserted once here instead of with an `is None` branch in six methods.
        """
        project = await self.projects.get(project_id)
        if project is None:
            raise TaskNotFoundError(details={"project_id": str(project_id)})
        return project

    async def _reload(self, task_id: uuid.UUID) -> Task:
        """Re-read a task with tags, project and assignee eager-loaded.

        Needed after every `commit()`: the identity map keeps the row but drops the
        relationship state, and touching a dropped relationship under asyncio raises
        `MissingGreenlet` instead of quietly loading it.
        """
        reloaded = await self.tasks.get_alive(task_id)
        if reloaded is None:  # pragma: no cover — only if deleted mid-flight
            raise TaskNotFoundError(details={"task_id": str(task_id)})
        return reloaded

    @staticmethod
    def _apply_status_transition(
        task: Task, new_status: TaskStatus, *, now: datetime
    ) -> TaskStatus:
        """Keep `started_at`/`completed_at` consistent with the status.

        The timestamps are derived, never accepted from the client: otherwise a task
        could be marked "completed last month" and break every due-date report.
        """
        task.started_at = task.started_at or (now if new_status is not TaskStatus.TODO else None)
        if new_status.is_terminal:
            task.completed_at = now
        else:
            task.completed_at = None
        if new_status in _NOTABLE_TRANSITIONS:
            logger.debug("task_transition", task_id=str(task.id), to=new_status.value)
        return new_status

    async def _resolve_tags(self, tag_ids: list[uuid.UUID]) -> list[Tag]:
        if not tag_ids:
            return []
        if len(tag_ids) > MAX_BULK_TAGS:
            raise TagLimitExceededError(details={"max_tags": MAX_BULK_TAGS})
        tags = await self.tags.get_many(tag_ids)
        if len(tags) != len(set(tag_ids)):
            raise ValidationError("One or more tag ids do not exist")
        return tags

    async def log_activity(
        self,
        task: Task,
        actor: User,
        action: ActivityAction,
        **payload: object,
    ) -> None:
        """Append one row to the activity log.

        The write happens inside a SAVEPOINT: a broken audit row must not be able to roll
        back the business transaction it is describing, and it must not leave the session
        unusable (a failed flush poisons the session until an explicit rollback).
        """
        row = ActivityLog(
            user_id=actor.id,
            task_id=task.id,
            action=action.value,
            entity_type="task",
            entity_id=task.id,
            payload=_jsonable({"title": task.title, **payload}),
        )
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except Exception:
            logger.warning(
                "activity_log_write_failed",
                task_id=str(task.id),
                action=action.value,
                exc_info=True,
            )

    @staticmethod
    async def _schedule_assignment_email(task: Task) -> None:
        """Ask the worker to notify the assignee; never raises.

        A dead broker must not fail a write that already succeeded, so the error is
        logged and the request continues.
        """
        if task.assignee_id is None:
            return
        await enqueue(
            "app.workers.tasks.notifications.send_task_assigned",
            str(task.id),
            queue=QUEUE_NOTIFICATIONS,
        )
