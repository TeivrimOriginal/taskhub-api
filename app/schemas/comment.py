"""Comment schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.schemas.common import BaseSchema
from app.schemas.user import UserSummary


class CommentCreate(BaseSchema):
    """Add a comment to a task."""

    body: str = Field(min_length=1, max_length=5000)


class CommentUpdate(BaseSchema):
    """Edit the body of your own comment."""

    body: str = Field(min_length=1, max_length=5000)


class CommentRead(BaseSchema):
    """Comment with its author."""

    id: uuid.UUID
    task_id: uuid.UUID
    author_id: uuid.UUID
    body: str
    created_at: datetime
    updated_at: datetime
    author: UserSummary | None = None
