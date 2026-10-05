"""The filter plumbing: query string in, repository keywords out.

`app.api.filters` is the one place where a filter is declared, so these tests are what
makes "add a filter" a safe one-line change. No database needed: the whole class is pure
bookkeeping.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.api.filters import (
    COMMENT_ALIASES,
    PROJECT_ALIASES,
    TAG_ALIASES,
    TASK_ALIASES,
    USER_ALIASES,
    FilterSet,
    comment_filters,
    project_filters,
    tag_filters,
    task_filters,
    user_filters,
)

pytestmark = pytest.mark.unit


def make(**values: Any) -> FilterSet:
    """A filter set with no alias table, for testing the mechanics only."""
    return FilterSet(values=values, aliases={})


class TestToRepository:
    def test_nothing_sent_means_nothing_forwarded(self) -> None:
        assert make(project_id=None, search=None).to_repository() == {}

    def test_a_value_that_was_sent_is_forwarded(self) -> None:
        assert make(search="apollo").to_repository() == {"search": "apollo"}

    def test_false_is_treated_as_not_sent(self) -> None:
        """A flag that is off must not reach the repository as ``overdue_only=False``."""
        assert make(overdue=False, search="x").to_repository() == {"search": "x"}

    def test_an_empty_list_is_treated_as_not_sent(self) -> None:
        assert make(tag=[]).to_repository() == {}

    def test_a_list_of_ids_is_forwarded_as_a_list(self) -> None:
        ids = [uuid.uuid4(), uuid.uuid4()]
        assert make(tag=ids).to_repository() == {"tag": ids}

    def test_false_is_kept_when_it_is_asked_for_explicitly(self) -> None:
        """``extra`` is for rules the service chose, so it is never filtered out."""
        assert make().to_repository(overdue_only=False) == {"overdue_only": False}

    def test_an_alias_renames_the_keyword(self) -> None:
        filters = FilterSet(values={"tag": ["a"], "overdue": True}, aliases=TASK_ALIASES)
        assert filters.to_repository() == {"tag_ids": ["a"], "overdue_only": True}

    def test_a_name_without_an_alias_stays_as_it_is(self) -> None:
        assert make(status="done").to_repository() == {"status": "done"}

    def test_extra_and_filters_are_merged(self) -> None:
        result = make(search="x").to_repository(owner_id=uuid.uuid4())
        assert "search" in result
        assert "owner_id" in result


class TestTake:
    def test_take_removes_the_flag(self) -> None:
        filters = make(mine=True, search="x")
        assert filters.take("mine") is True
        assert "mine" not in filters.to_repository()

    def test_take_returns_false_when_the_flag_is_off(self) -> None:
        assert make(mine=False).take("mine") is False

    def test_take_is_safe_for_a_name_that_is_not_there(self) -> None:
        assert make().take("nothing") is False

    def test_a_taken_flag_can_be_taken_only_once(self) -> None:
        filters = make(mine=True)
        assert filters.take("mine") is True
        assert filters.take("mine") is False


class TestTruthiness:
    def test_a_set_with_something_in_it_is_true(self) -> None:
        assert bool(make(search="x")) is True

    def test_an_empty_set_is_false(self) -> None:
        assert bool(make(search=None, overdue=False, tag=[])) is False


class TestAliasTables:
    @pytest.mark.parametrize(
        ("table", "source"),
        [
            (TASK_ALIASES, "task"),
            (PROJECT_ALIASES, "project"),
            (USER_ALIASES, "user"),
            (TAG_ALIASES, "tag"),
            (COMMENT_ALIASES, "comment"),
        ],
    )
    def test_every_table_maps_only_onto_real_repository_keywords(
        self,
        table: dict[str, str],
        source: str,
    ) -> None:
        """An alias that points at nothing is a filter that silently does nothing."""
        known = {
            "project_id",
            "owner_id",
            "status",
            "priority",
            "assignee_id",
            "search",
            "tag_ids",
            "overdue_only",
            "due_before",
            "created_from",
            "is_active",
            "include_deleted",
            "role",
            "task_id",
            "author_id",
        }
        unknown = set(table.values()) - known
        assert not unknown, f"{source} filters point at unknown keywords: {unknown}"

    def test_the_task_table_covers_the_documented_query_names(self) -> None:
        # These are the names in the URL, so they are part of the public API.
        assert set(TASK_ALIASES) == {
            "project_id",
            "status",
            "priority",
            "assignee_id",
            "search",
            "tag",
            "overdue",
            "due_before",
        }


class TestDeclaredFilters:
    """The dependency functions themselves: they only build a set."""

    @pytest.mark.asyncio
    async def test_task_filters_rename_the_colliding_parameter(self) -> None:
        # A parameter named like a path placeholder becomes a *path* parameter, which
        # FastAPI rejects when it also has a default. `?project_id=` must still work.
        project_id = uuid.uuid4()
        filters = await task_filters(
            filter_project_id=project_id,
            status=None,
            priority=None,
            assignee_id=None,
            mine=False,
            search=None,
            tag=None,
            overdue=False,
            due_before=None,
        )
        assert filters.to_repository() == {"project_id": project_id}

    @pytest.mark.asyncio
    async def test_task_filters_keep_the_mine_flag_out_of_the_keywords(self) -> None:
        filters = await task_filters(
            filter_project_id=None,
            status=None,
            priority=None,
            assignee_id=None,
            mine=True,
            search=None,
            tag=None,
            overdue=False,
            due_before=None,
        )
        assert filters.take("mine") is True
        assert filters.to_repository() == {}

    @pytest.mark.asyncio
    async def test_project_filters(self) -> None:
        filters = await project_filters(search="apollo", is_active=True)
        assert filters.to_repository() == {"search": "apollo", "is_active": True}

    @pytest.mark.asyncio
    async def test_user_filters(self) -> None:
        filters = await user_filters(
            search="dev",
            role=None,
            is_active=None,
            include_deleted=True,
        )
        assert filters.to_repository() == {"search": "dev", "include_deleted": True}

    @pytest.mark.asyncio
    async def test_tag_filters(self) -> None:
        assert (await tag_filters(search="back")).to_repository() == {"search": "back"}

    @pytest.mark.asyncio
    async def test_comment_filters(self) -> None:
        task_id = uuid.uuid4()
        filters = await comment_filters(filter_task_id=task_id, author_id=None)
        assert filters.to_repository() == {"task_id": task_id}

    @pytest.mark.asyncio
    async def test_no_filter_means_an_empty_set(self) -> None:
        filters = await tag_filters(search=None)
        assert bool(filters) is False
