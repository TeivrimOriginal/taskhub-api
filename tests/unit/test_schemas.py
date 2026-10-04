"""Schema-level validation and the shared pagination envelope."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.core.constants import TaskPriority, TaskStatus
from app.schemas.common import PaginatedResponse
from app.schemas.project import ProjectCreate, ProjectUpdate
from app.schemas.tag import TagCreate, TagUpdate, normalise_tag_name
from app.schemas.task import TaskCreate, TaskUpdate
from app.schemas.user import PasswordChange, UserCreate, UserUpdate

pytestmark = pytest.mark.unit


class TestUserSchemas:
    def test_username_is_lowercased(self) -> None:
        payload = UserCreate(email="a@b.dev", username="DeV_1", password=PASSWORD)
        assert payload.username == "dev_1"

    @pytest.mark.parametrize("username", ["ab", "with space", "юзер", "a" * 65, "bad/slash"])
    def test_rejects_bad_usernames(self, username: str) -> None:
        with pytest.raises(ValidationError):
            UserCreate(email="a@b.dev", username=username, password=PASSWORD)

    def test_rejects_a_weak_password(self) -> None:
        with pytest.raises(ValidationError, match="mix lower case"):
            UserCreate(email="a@b.dev", username="dev", password="alllowercase")

    def test_rejects_a_short_password(self) -> None:
        with pytest.raises(ValidationError):
            UserCreate(email="a@b.dev", username="dev", password="Ab1!")

    def test_rejects_an_invalid_email(self) -> None:
        with pytest.raises(ValidationError):
            UserCreate(email="not-an-email", username="dev", password=PASSWORD)

    def test_rejects_unknown_fields(self) -> None:
        """`extra="forbid"` is what makes privilege escalation impossible via the body."""
        payload: dict[str, object] = {
            "email": "a@b.dev",
            "username": "dev",
            "password": PASSWORD,
            "is_superuser": True,
        }
        with pytest.raises(ValidationError):
            UserCreate.model_validate(payload)

    def test_password_change_must_differ(self) -> None:
        payload = PasswordChange(current_password=PASSWORD, new_password=PASSWORD)
        with pytest.raises(ValueError, match="must differ"):
            payload.assert_changed()

    def test_user_update_allows_partial_patches(self) -> None:
        payload = UserUpdate(full_name="New Name")
        assert payload.model_dump(exclude_unset=True) == {"full_name": "New Name"}


class TestProjectSchemas:
    def test_name_is_collapsed_to_single_spaces(self) -> None:
        assert ProjectCreate(name="  Release   1.0  ").name == "Release 1.0"

    def test_rejects_a_blank_name(self) -> None:
        with pytest.raises(ValidationError):
            ProjectCreate(name="   ")

    @pytest.mark.parametrize("color", ["red", "#12345", "4F46E5", "#GGGGGG"])
    def test_rejects_a_bad_color(self, color: str) -> None:
        with pytest.raises(ValidationError):
            ProjectCreate(name="ok", color=color)

    def test_color_is_normalised_to_lower_case(self) -> None:
        assert ProjectCreate(name="ok", color="#4F46E5").color == "#4f46e5"

    def test_update_accepts_an_empty_patch(self) -> None:
        assert ProjectUpdate().model_dump(exclude_unset=True) == {}


class TestTagSchemas:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Backend", "backend"),
            ("  urgent  ", "urgent"),
            ("needs review", "needs-review"),
        ],
    )
    def test_names_are_normalised(self, raw: str, expected: str) -> None:
        assert normalise_tag_name(raw) == expected

    @pytest.mark.parametrize("raw", ["", "  ", "-leading", "with space!", "x" * 60])
    def test_rejects_unusable_names(self, raw: str) -> None:
        with pytest.raises(ValidationError):
            TagCreate(name=raw)

    def test_update_keeps_none_as_noop(self) -> None:
        assert TagUpdate(name=None).name is None


class TestTaskSchemas:
    def test_defaults_match_the_model(self) -> None:
        payload = TaskCreate(project_id=uuid.uuid4(), title="t")
        assert payload.status is TaskStatus.TODO
        assert payload.priority is TaskPriority.MEDIUM
        assert payload.position == 0
        assert payload.tag_ids == []

    def test_rejects_a_naive_due_date(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            TaskCreate(
                project_id=uuid.uuid4(),
                title="t",
                due_date=datetime(2030, 1, 1),  # noqa: DTZ001 — deliberately naive
            )

    def test_accepts_an_aware_due_date(self) -> None:
        payload = TaskCreate(
            project_id=uuid.uuid4(),
            title="t",
            due_date=datetime(2030, 1, 1, tzinfo=UTC),
        )
        assert payload.due_date is not None

    def test_rejects_a_negative_position(self) -> None:
        with pytest.raises(ValidationError):
            TaskUpdate(position=-1)

    def test_rejects_a_negative_estimate(self) -> None:
        with pytest.raises(ValidationError):
            TaskUpdate(estimate_minutes=-30)


PASSWORD = "Str0ngPassw0rd!"


class TestPaginationEnvelope:
    def test_pages_is_ceil_division(self) -> None:
        envelope = PaginatedResponse[int].create([1, 2, 3], total=7, page=1, size=3)
        assert envelope.pages == 3
        assert envelope.has_next is True
        assert envelope.has_prev is False

    def test_middle_page_has_both_flags(self) -> None:
        envelope = PaginatedResponse[int].create([4, 5, 6], total=7, page=2, size=3)
        assert envelope.has_next is True
        assert envelope.has_prev is True

    def test_last_page_has_no_next(self) -> None:
        envelope = PaginatedResponse[int].create([7], total=7, page=3, size=3)
        assert envelope.has_next is False
        assert envelope.has_prev is True

    def test_empty_collection_reports_zero_pages(self) -> None:
        envelope = PaginatedResponse[int].create([], total=0, page=1, size=20)
        assert envelope.pages == 0
        assert envelope.has_next is False
        assert envelope.has_prev is False

    def test_exact_multiple_has_no_trailing_empty_page(self) -> None:
        envelope = PaginatedResponse[int].create([1, 2], total=4, page=2, size=2)
        assert envelope.pages == 2
        assert envelope.has_next is False

    def test_page_beyond_the_end_is_empty_but_valid(self) -> None:
        envelope = PaginatedResponse[int].create([], total=4, page=99, size=2)
        assert envelope.items == []
        assert envelope.pages == 2
        assert envelope.has_next is False
