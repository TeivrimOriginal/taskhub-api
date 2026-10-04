"""Tag schemas."""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import Field, field_validator

from app.schemas.common import BaseSchema

TAG_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,49}$")
HEX_COLOR_RE = r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$"


def normalise_tag_name(value: str) -> str:
    """Tags are stored lower-cased and slug-like so uniqueness is predictable."""
    normalised = re.sub(r"\s+", "-", value.strip().lower())
    if not TAG_NAME_RE.match(normalised):
        raise ValueError(
            "tag name must start with a letter or digit and contain only lower-case "
            "letters, digits, dashes or underscores (max 50 characters)"
        )
    return normalised


class TagCreate(BaseSchema):
    """Create (or reuse) a tag."""

    name: str = Field(min_length=1, max_length=50, examples=["backend"])
    color: str = Field(default="#0EA5E9", pattern=HEX_COLOR_RE)

    @field_validator("name")
    @classmethod
    def _normalise_name(cls, value: str) -> str:
        return normalise_tag_name(value)


class TagUpdate(BaseSchema):
    """Partial update."""

    name: str | None = Field(default=None, min_length=1, max_length=50)
    color: str | None = Field(default=None, pattern=HEX_COLOR_RE)

    @field_validator("name")
    @classmethod
    def _normalise_name(cls, value: str | None) -> str | None:
        return normalise_tag_name(value) if value is not None else None


class TagRead(BaseSchema):
    """Tag representation."""

    id: uuid.UUID
    name: str
    color: str
    created_at: datetime


class TagAttach(BaseSchema):
    """Attach an existing tag to a task."""

    tag_id: uuid.UUID
