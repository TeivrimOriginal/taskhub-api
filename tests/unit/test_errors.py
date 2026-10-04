"""Domain exception mapping and the error envelope helper."""

from __future__ import annotations

import pytest

from app.core.constants import ErrorCode, Role, TaskPriority, TaskStatus
from app.core.errors import (
    AppError,
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    RefreshTokenReuseError,
    ServiceUnavailableError,
    TagLimitExceededError,
    ValidationError as DomainValidationError,
)

pytestmark = pytest.mark.unit


class TestExceptionHierarchy:
    def test_every_domain_error_is_an_app_error(self) -> None:
        for error_type in (
            AuthenticationError,
            PermissionDeniedError,
            NotFoundError,
            ConflictError,
            RateLimitError,
            ServiceUnavailableError,
            TagLimitExceededError,
        ):
            assert issubclass(error_type, AppError)

    @pytest.mark.parametrize(
        ("error_type", "expected_status"),
        [
            (AuthenticationError, 401),
            (PermissionDeniedError, 403),
            (NotFoundError, 404),
            (ConflictError, 409),
            (DomainValidationError, 422),
            (RateLimitError, 429),
            (ServiceUnavailableError, 503),
            (AppError, 500),
        ],
    )
    def test_status_codes_match_the_class(
        self, error_type: type[AppError], expected_status: int
    ) -> None:
        assert error_type().status_code == expected_status

    def test_reuse_error_is_an_authentication_error(self) -> None:
        assert issubclass(RefreshTokenReuseError, AuthenticationError)

    def test_custom_message_and_details(self) -> None:
        error = NotFoundError("Task 42 is missing", details={"task_id": 42})
        payload = error.to_dict()
        assert payload["message"] == "Task 42 is missing"
        assert payload["details"] == {"task_id": 42}
        assert payload["code"] == ErrorCode.NOT_FOUND.value

    def test_details_default_to_an_empty_dict(self) -> None:
        assert NotFoundError().to_dict()["details"] == {}

    def test_status_and_code_can_be_overridden(self) -> None:
        error = AppError("boom", code=ErrorCode.CONFLICT, status_code=418)
        assert error.status_code == 418
        assert error.code is ErrorCode.CONFLICT

    def test_message_falls_back_to_the_class_default(self) -> None:
        assert NotFoundError().message == "Resource not found"

    def test_str_is_the_message(self) -> None:
        assert str(ConflictError("duplicate")) == "duplicate"


class TestEnums:
    def test_roles_are_ordered(self) -> None:
        assert Role.USER.level < Role.MODERATOR.level < Role.ADMIN.level

    def test_terminal_statuses(self) -> None:
        assert TaskStatus.DONE.is_terminal is True
        assert TaskStatus.CANCELLED.is_terminal is True
        assert TaskStatus.TODO.is_terminal is False
        assert TaskStatus.IN_REVIEW.is_terminal is False

    def test_priority_weights(self) -> None:
        assert TaskPriority.LOW.weight < TaskPriority.URGENT.weight
        assert TaskPriority.MEDIUM.weight == 1

    def test_enum_values_are_stable_strings(self) -> None:
        """The database stores these exact strings, so they are part of the contract."""
        assert Role.ADMIN.value == "admin"
        assert TaskStatus.IN_PROGRESS.value == "in_progress"
        assert TaskPriority.URGENT.value == "urgent"
