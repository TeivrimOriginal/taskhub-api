"""Project schemas."""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import Field, field_validator

from app.schemas.common import BaseSchema

HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


class ProjectCreate(BaseSchema):
    """Create a project; the owner is taken from the access token, not the body."""

    name: str = Field(min_length=1, max_length=120, examples=["Release 1.0"])
    description: str | None = Field(default=None, max_length=5000)
    color: str = Field(default="#4F46E5", pattern=r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("name must contain at least one non-whitespace character")
        return cleaned

    @field_validator("color")
    @classmethod
    def _validate_color(cls, value: str) -> str:
        if not HEX_COLOR_RE.match(value):
            raise ValueError("color must be a hex value such as #4F46E5")
        return value.lower()


class ProjectUpdate(BaseSchema):
    """Partial update; unset fields are left untouched."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=5000)
    color: str | None = Field(default=None, pattern=r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
    is_active: bool | None = None


class ProjectRead(BaseSchema):
    """Project with its denormalised task counters."""

    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    description: str | None
    color: str
    is_active: bool
    tasks_total: int
    tasks_done: int
    completion_rate: float
    last_activity_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ProjectSummary(BaseSchema):
    """Compact project shape embedded in task responses."""

    id: uuid.UUID
    name: str
    color: str
