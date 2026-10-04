"""Shared response envelopes.

Two envelopes and nothing else, so a client can write one error handler and one
pagination handler and trust they cover the whole API.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

PageNumber = Annotated[int, Field(ge=1, le=10_000, description="1-based page number")]
PageSize = Annotated[int, Field(ge=1, le=100, description="Items per page (max 100)")]


class BaseSchema(BaseModel):
    """Base for every schema: read from ORM attributes, reject unknown fields."""

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class Message(BaseSchema):
    """Plain textual acknowledgement."""

    message: str
    detail: dict[str, str | int | bool] | None = None


class PaginationMeta(BaseSchema):
    """Everything a client needs to walk a paginated collection."""

    total: int = Field(ge=0, description="Total number of rows matching the filters")
    page: int = Field(ge=1)
    size: int = Field(ge=1)
    pages: int = Field(ge=0)
    has_next: bool
    has_prev: bool


class PaginatedResponse[T](BaseSchema):
    """`{items, total, page, size, pages, has_next, has_prev}`."""

    items: list[T]
    total: int
    page: int
    size: int
    pages: int
    has_next: bool
    has_prev: bool

    @classmethod
    def create(
        cls,
        items: list[T],
        *,
        total: int,
        page: int,
        size: int,
    ) -> PaginatedResponse[T]:
        """Build the envelope, deriving `pages`/`has_next`/`has_prev` consistently."""
        pages = (total + size - 1) // size if size else 0
        return cls(
            items=items,
            total=total,
            page=page,
            size=size,
            pages=pages,
            has_next=page < pages,
            has_prev=page > 1,
        )


class ErrorBody(BaseSchema):
    """Machine-readable error payload."""

    code: str
    message: str
    details: dict[str, object] = Field(default_factory=dict)


class ErrorResponse(BaseSchema):
    """The single error shape every failing endpoint returns."""

    error: ErrorBody
