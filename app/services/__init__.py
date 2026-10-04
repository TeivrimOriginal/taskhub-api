"""Services — business rules, transactions and cache orchestration.

A service is the only place allowed to `commit()`. It receives repositories, composes
them, raises domain errors and returns ORM objects; it knows nothing about HTTP.
"""

from app.services.auth import AuthService
from app.services.comment import CommentService
from app.services.email import EmailService
from app.services.project import ProjectService
from app.services.stats import StatsService
from app.services.tag import TagService
from app.services.task import TaskService
from app.services.user import UserService

__all__ = [
    "AuthService",
    "CommentService",
    "EmailService",
    "ProjectService",
    "StatsService",
    "TagService",
    "TaskService",
    "UserService",
]
