"""Task schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field, model_validator

from app.core.constants import MAX_BULK_TAGS, TaskPriority, TaskStatus
from app.schemas.common import BaseSchema
from app.schemas.project import ProjectSummary
from app.schemas.tag import TagRead
from app.schemas.user import UserSummary


class TaskCreate(BaseSchema):
    """Create a task inside an existing project."""

    project_id: uuid.UUID
    title: str = Field(min_length=1, max_length=200, examples=["Write integration tests"])
    description: str | None = Field(default=None, max_length=10_000)
    status: TaskStatus = TaskStatus.TODO
    priority: TaskPriority = TaskPriority.MEDIUM
    assignee_id: uuid.UUID | None = None
    due_date: datetime | None = None
    position: int = Field(default=0, ge=0, le=100_000)
    estimate_minutes: int | None = Field(default=None, ge=0, le=100_000)
    tag_ids: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_BULK_TAGS)

    @model_validator(mode="after")
    def _validate_dates(self) -> TaskCreate:
        if self.due_date is not None and self.due_date.tzinfo is None:
            raise ValueError("due_date must be timezone-aware")
        return self


class TaskUpdate(BaseSchema):
    """Partial update — every field optional."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    assignee_id: uuid.UUID | None = None
    due_date: datetime | None = None
    position: int | None = Field(default=None, ge=0, le=100_000)
    estimate_minutes: int | None = Field(default=None, ge=0, le=100_000)


class TaskRead(BaseSchema):
    """Task with its loaded relations."""

    id: uuid.UUID
    project_id: uuid.UUID
    assignee_id: uuid.UUID | None
    title: str
    description: str | None
    status: TaskStatus
    priority: TaskPriority
    position: int
    due_date: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    estimate_minutes: int | None
    created_at: datetime
    updated_at: datetime
    project: ProjectSummary | None = None
    assignee: UserSummary | None = None
    tags: list[TagRead] = Field(default_factory=list)
    is_overdue: bool = False
