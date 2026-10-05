"""One place for the OpenAPI error blocks and the pagination envelope.

Why this module exists
----------------------
Two shapes of response repeat in this API, and both used to be written out again in every
router:

* the ``responses={...}`` dictionary that documents 401/403/404/409 in Swagger;
* the four lines that turn ``(items, total)`` into ``PaginatedResponse``.

With six routers the copy-paste was already inconsistent — one router documented 409 and
the others did not — and the next entity would have been the seventh copy. Now a router
imports a name and says nothing about envelope shape.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.api.deps import PaginationParams
from app.schemas.common import ErrorResponse, PaginatedResponse


# --------------------------------------------------------------- OpenAPI blocks
def _block(description: str) -> dict[str, object]:
    """One documented error response."""
    return {"model": ErrorResponse, "description": description}


#: For an endpoint that only needs a logged-in caller.
AUTH_ERRORS: dict[int | str, dict[str, object]] = {
    401: _block("Not authenticated"),
}

#: For an endpoint that also has per-row rules.
ACCESS_ERRORS: dict[int | str, dict[str, object]] = {
    401: _block("Not authenticated"),
    403: _block("Your role may not do this"),
    404: _block("The row does not exist"),
}

#: For an endpoint that also writes.
WRITE_ERRORS: dict[int | str, dict[str, object]] = {
    **ACCESS_ERRORS,
    409: _block("The change conflicts with existing data"),
    422: _block("The payload failed validation"),
}


# ----------------------------------------------------------------- the envelope
def to_schema[ModelT: BaseModel](item: Any, schema: type[ModelT]) -> ModelT:
    """Turn one ORM row into one response object.

    ``extra="forbid"`` on the response schemas is what makes this safe: a field the schema
    does not know about cannot slip into a response by accident.
    """
    return schema.model_validate(item, from_attributes=True)


def to_schemas[ModelT: BaseModel](items: list[Any], schema: type[ModelT]) -> list[ModelT]:
    """Turn a list of ORM rows into a list of response objects."""
    return [to_schema(item, schema) for item in items]


def build_page[ModelT: BaseModel](
    items: list[Any],
    total: int,
    pagination: PaginationParams,
    schema: type[ModelT],
) -> PaginatedResponse[ModelT]:
    """Build the one pagination envelope every list endpoint returns.

    Call it like this, and every list endpoint in the API answers with the same shape::

        return build_page(items, total, pagination, ProjectRead)

    Args:
        items: the rows for this page, already filtered and sorted.
        total: how many rows match the filters, ignoring the page.
        pagination: the ``?page=&size=`` values from the request.
        schema: the response class for one row.
    """
    return PaginatedResponse[ModelT].create(
        to_schemas(items, schema),
        total=total,
        page=pagination.page,
        size=pagination.size,
    )
