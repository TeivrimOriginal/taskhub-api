"""Domain constants shared by models, schemas, services and the API layer."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """Application roles, ordered from least to most privileged."""

    USER = "user"
    MODERATOR = "moderator"
    ADMIN = "admin"

    @property
    def level(self) -> int:
        """Numeric weight used for `>=` privilege comparisons."""
        return _ROLE_LEVELS[self]


_ROLE_LEVELS: dict[Role, int] = {
    Role.USER: 0,
    Role.MODERATOR: 1,
    Role.ADMIN: 2,
}

STAFF_ROLES: frozenset[Role] = frozenset({Role.MODERATOR, Role.ADMIN})


class TaskStatus(StrEnum):
    """Lifecycle of a task."""

    TODO = "todo"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    DONE = "done"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {TaskStatus.DONE, TaskStatus.CANCELLED}


class TaskPriority(StrEnum):
    """Business priority, ordered from lowest to highest."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"

    @property
    def weight(self) -> int:
        return _PRIORITY_WEIGHTS[self]


_PRIORITY_WEIGHTS: dict[TaskPriority, int] = {
    TaskPriority.LOW: 0,
    TaskPriority.MEDIUM: 1,
    TaskPriority.HIGH: 2,
    TaskPriority.URGENT: 3,
}


class TokenType(StrEnum):
    """`typ` claim of a JWT."""

    ACCESS = "access"
    REFRESH = "refresh"


class ActivityAction(StrEnum):
    """Names stored in the activity log."""

    PROJECT_CREATED = "project.created"
    PROJECT_UPDATED = "project.updated"
    PROJECT_DELETED = "project.deleted"
    TASK_CREATED = "task.created"
    TASK_UPDATED = "task.updated"
    TASK_COMPLETED = "task.completed"
    TASK_DELETED = "task.deleted"
    TASK_ASSIGNED = "task.assigned"
    COMMENT_CREATED = "comment.created"
    COMMENT_DELETED = "comment.deleted"


class ErrorCode(StrEnum):
    """Stable, machine-readable error codes returned to clients."""

    VALIDATION_ERROR = "validation_error"
    AUTHENTICATION_ERROR = "authentication_error"
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    RATE_LIMITED = "rate_limited"
    INTERNAL_ERROR = "internal_error"
    SERVICE_UNAVAILABLE = "service_unavailable"


class CacheEntity(StrEnum):
    """The kind of row a write touched.

    Services pass this to `invalidate_entities()` instead of building cache key patterns
    by hand, so "which cached views does a task write change?" has exactly one answer.
    """

    USER = "user"
    PROJECT = "project"
    TASK = "task"
    COMMENT = "comment"
    TAG = "tag"


# Redis key namespaces — one place to see every cache key the app can create.
CACHE_PREFIX = "taskhub"
KEY_USER = f"{CACHE_PREFIX}:user"
KEY_STATS = f"{CACHE_PREFIX}:stats"
KEY_PROJECT = f"{CACHE_PREFIX}:project"
KEY_TASK = f"{CACHE_PREFIX}:task"
KEY_TAG = f"{CACHE_PREFIX}:tag"
KEY_RATE_LIMIT = f"{CACHE_PREFIX}:rl"

# Celery queue routing.
QUEUE_DEFAULT = "default"
QUEUE_NOTIFICATIONS = "notifications"
QUEUE_MAINTENANCE = "maintenance"

# Default pagination guardrails.
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

# How many related rows the API may fetch at once.
MAX_BULK_TAGS = 10
