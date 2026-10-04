"""Domain exception hierarchy.

Services raise these; `app.api.errors` translates them into HTTP responses. Services
therefore never import `fastapi`, which keeps them unit-testable without a client.
"""

from __future__ import annotations

from typing import Any

from app.core.constants import ErrorCode


class AppError(Exception):
    """Base class for every error the application raises deliberately."""

    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    status_code: int = 500
    message: str = "Unexpected server error"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        code: ErrorCode | None = None,
        status_code: int | None = None,
    ) -> None:
        self.message = message or self.message
        self.details: dict[str, Any] = details or {}
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable representation used by the error handler."""
        return {
            "code": self.code.value,
            "message": self.message,
            "details": self.details,
        }


# --------------------------------------------------------------------- 4xx
class ValidationError(AppError):
    """Payload failed a business rule that pydantic cannot express."""

    code = ErrorCode.VALIDATION_ERROR
    status_code = 422
    message = "Request payload is invalid"


class AuthenticationError(AppError):
    """Credentials or a token are missing, malformed or expired."""

    code = ErrorCode.AUTHENTICATION_ERROR
    status_code = 401
    message = "Could not validate credentials"


class PermissionDeniedError(AppError):
    """The caller is authenticated but not allowed to perform the action."""

    code = ErrorCode.PERMISSION_DENIED
    status_code = 403
    message = "You do not have permission to perform this action"


class NotFoundError(AppError):
    """Requested entity does not exist (or is soft-deleted)."""

    code = ErrorCode.NOT_FOUND
    status_code = 404
    message = "Resource not found"


class ConflictError(AppError):
    """The write conflicts with the current state (unique constraint, etc.)."""

    code = ErrorCode.CONFLICT
    status_code = 409
    message = "Resource already exists"


class RateLimitError(AppError):
    """Too many requests from the same client."""

    code = ErrorCode.RATE_LIMITED
    status_code = 429
    message = "Too many requests"


# --------------------------------------------------------------------- 5xx
class ServiceUnavailableError(AppError):
    """A dependency (database, cache) is not reachable."""

    code = ErrorCode.SERVICE_UNAVAILABLE
    status_code = 503
    message = "Service temporarily unavailable"


# -------------------------------------------------------- entity-specific
class UserNotFoundError(NotFoundError):
    message = "User not found"


class UserAlreadyExistsError(ConflictError):
    message = "A user with this e-mail or username already exists"


class InactiveUserError(AuthenticationError):
    message = "This account is deactivated"


class InvalidCredentialsError(AuthenticationError):
    """Deliberately vague: never reveal whether the e-mail exists."""

    message = "Incorrect e-mail or password"


class InvalidTokenError(AuthenticationError):
    message = "Token is invalid or has expired"


class RefreshTokenReuseError(AuthenticationError):
    """A consumed refresh token was presented again — treat as theft."""

    message = "Refresh token has already been used; all sessions have been revoked"


class ProjectNotFoundError(NotFoundError):
    message = "Project not found"


class TaskNotFoundError(NotFoundError):
    message = "Task not found"


class CommentNotFoundError(NotFoundError):
    message = "Comment not found"


class TagNotFoundError(NotFoundError):
    message = "Tag not found"


class TagLimitExceededError(ValidationError):
    message = "A task cannot carry more tags than the configured maximum"
