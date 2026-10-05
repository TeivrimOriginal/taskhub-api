"""Every list filter is declared here, once.

Why this module exists
----------------------
Before this file a filter had to be written in three places, and forgetting any one of
them produced a bug that type checking cannot see:

1. the router declared it as ``Query(...)`` so the docs could show it;
2. the service accepted it as a named argument and passed it on;
3. the repository knew what to do with it.

So a new filter meant three edits and three chances to spell the name differently. Here a
filter is declared **once**, in one function, with a signature FastAPI can turn into real
OpenAPI. Adding ``?assignee_id=`` to the task list is a one-line change below, and nothing
else in the codebase has to move.

How a filter flows through the layers
-------------------------------------
    router  ->  TaskFilters.to_repository()  ->  service  ->  repository
    (name)      (dict of names to values)       (**filters)   (**filters)

The middle step is the important one: the router never spells a repository keyword, so a
rename cannot half-happen. ``None`` values are dropped, which is what lets a caller send
two filters and have the repository see only the ones that were set.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from fastapi import Query

from app.core.constants import Role, TaskPriority, TaskStatus


@dataclass(frozen=True, slots=True)
class FilterSet:
    """The filters of one list endpoint, plus the rules for turning them into keywords.

    Attributes:
        values: the raw values that came from the query string.
        aliases: query-parameter name to repository keyword name.

    Only the keys that were actually sent are kept, so the repository does not have to
    know that ``?status=`` and ``?priority=`` are optional.
    """

    values: dict[str, Any]
    aliases: dict[str, str]

    def to_repository(self, **extra: Any) -> dict[str, Any]:
        """Return repository keywords: only what was sent, plus the fixed extras.

        ``extra`` carries the filters the *service* decided on — the owner scope, for
        example — which the client has no business choosing.
        """
        result: dict[str, Any] = dict(extra)
        for name, value in self.values.items():
            if value is None or value == [] or value is False:
                continue
            result[self.aliases.get(name, name)] = value
        return result

    def take(self, name: str) -> bool:
        """Take a flag out of the filter set and return it.

        Use this for a query parameter that is a *rule* and not a filter. ``?mine=true``
        is the example: there is no ``mine`` column, the service decides what the flag
        means, so it has to leave the set here. Otherwise it would reach the repository as
        a keyword the repository does not accept.
        """
        return bool(self.values.pop(name, False))

    def __bool__(self) -> bool:
        """True when the client sent at least one filter."""
        return any(
            value is not None and value != [] and value is not False
            for value in self.values.values()
        )


#: name in the URL -> keyword the repository expects.
TASK_ALIASES: dict[str, str] = {
    "project_id": "project_id",
    "status": "status",
    "priority": "priority",
    "assignee_id": "assignee_id",
    "search": "search",
    "tag": "tag_ids",
    "overdue": "overdue_only",
    "due_before": "due_before",
}

PROJECT_ALIASES: dict[str, str] = {
    "search": "search",
    "is_active": "is_active",
}

USER_ALIASES: dict[str, str] = {
    "search": "search",
    "role": "role",
    "is_active": "is_active",
    "include_deleted": "include_deleted",
}

TAG_ALIASES: dict[str, str] = {"search": "search"}

COMMENT_ALIASES: dict[str, str] = {"task_id": "task_id", "author_id": "author_id"}


async def task_filters(
    # Read the two rules in this signature before you add a parameter.
    #
    # 1. A parameter whose name matches a `{placeholder}` in the route path becomes a PATH
    #    parameter, not a query parameter. `list_project_tasks` lives at
    #    `/projects/{project_id}/tasks`, so a filter called `project_id` would be declared
    #    as a path parameter with a default, and FastAPI refuses that at import time. The
    #    name is therefore `filter_project_id` and the public query name is set with
    #    `alias`, which is the same URL for the client and a different name in the code.
    # 2. `mine` and `overdue` are flags. They default to `False` and are dropped from the
    #    repository keywords, so a caller who does not send them changes nothing.
    filter_project_id: Annotated[
        uuid.UUID | None,
        Query(alias="project_id", description="Only tasks of this project"),
    ] = None,
    status: Annotated[
        TaskStatus | None,
        Query(description="Filter by status"),
    ] = None,
    priority: Annotated[TaskPriority | None, Query(description="Filter by priority")] = None,
    assignee_id: Annotated[uuid.UUID | None, Query(description="Filter by assignee")] = None,
    mine: Annotated[bool, Query(description="Only tasks assigned to me")] = False,
    search: Annotated[
        str | None, Query(max_length=200, description="Match title or description")
    ] = None,
    tag: Annotated[
        list[uuid.UUID] | None,
        Query(description="Repeatable: only tasks carrying every listed tag"),
    ] = None,
    overdue: Annotated[bool, Query(description="Only tasks past their due date")] = False,
    due_before: Annotated[
        datetime | None, Query(description="Only tasks due at or before this moment")
    ] = None,
) -> FilterSet:
    """Declare every filter of ``GET /tasks`` in one place.

    A router uses it like this::

        @router.get("", response_model=PaginatedResponse[TaskRead])
        async def list_tasks(filters: TaskFilters, ...):
            items, total = await service.list_for_user(
                user=current_user, **pagination.as_kwargs(), **filters.to_repository()
            )

    To add a filter: add one line here, and one line in the ``_ALIASES`` table if the
    repository calls it something else. Nothing else in the codebase has to move.
    """
    return FilterSet(
        values={
            "project_id": filter_project_id,
            "status": status,
            "priority": priority,
            "assignee_id": assignee_id,
            "mine": mine,
            "search": search,
            "tag": list(tag) if tag else None,
            "overdue": overdue,
            "due_before": due_before,
        },
        aliases=TASK_ALIASES,
    )


async def project_filters(
    search: Annotated[
        str | None, Query(max_length=120, description="Match name or description")
    ] = None,
    is_active: Annotated[bool | None, Query(description="Filter by active flag")] = None,
) -> FilterSet:
    """Declare every filter of ``GET /projects`` in one place."""
    return FilterSet(
        values={"search": search, "is_active": is_active},
        aliases=PROJECT_ALIASES,
    )


async def user_filters(
    search: Annotated[
        str | None, Query(max_length=120, description="Match e-mail, username or name")
    ] = None,
    role: Annotated[Role | None, Query(description="Filter by role")] = None,
    is_active: Annotated[bool | None, Query(description="Filter by activation flag")] = None,
    include_deleted: Annotated[bool, Query(description="Include soft-deleted accounts")] = False,
) -> FilterSet:
    """Declare every filter of ``GET /users`` in one place."""
    return FilterSet(
        values={
            "search": search,
            "role": role,
            "is_active": is_active,
            "include_deleted": include_deleted,
        },
        aliases=USER_ALIASES,
    )


async def tag_filters(
    search: Annotated[str | None, Query(max_length=50, description="Prefix match")] = None,
) -> FilterSet:
    """Declare every filter of ``GET /tags`` in one place."""
    return FilterSet(values={"search": search}, aliases=TAG_ALIASES)


async def comment_filters(
    # `filter_task_id` for the same reason as `task_filters`: a name that matches a path
    # placeholder becomes a path parameter. See the long note in `task_filters`.
    filter_task_id: Annotated[
        uuid.UUID | None,
        Query(alias="task_id", description="Only comments of this task"),
    ] = None,
    author_id: Annotated[
        uuid.UUID | None, Query(description="Only comments by this author")
    ] = None,
) -> FilterSet:
    """Declare every filter of the comment list."""
    return FilterSet(
        values={"task_id": filter_task_id, "author_id": author_id},
        aliases=COMMENT_ALIASES,
    )
