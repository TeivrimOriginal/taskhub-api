"""Repositories — the only layer that speaks SQL.

A thin generic base gives `get/find/insert/update/delete/count/paginate`; concrete
subclasses add the queries their entity needs. Nothing above this layer imports
`sqlalchemy`.
"""

from app.repositories.base import BaseRepository
from app.repositories.comment import CommentRepository
from app.repositories.project import ProjectRepository
from app.repositories.refresh_token import RefreshTokenRepository
from app.repositories.stats import StatsRepository
from app.repositories.tag import TagRepository
from app.repositories.task import TaskRepository
from app.repositories.user import UserRepository

__all__ = [
    "BaseRepository",
    "CommentRepository",
    "ProjectRepository",
    "RefreshTokenRepository",
    "StatsRepository",
    "TagRepository",
    "TaskRepository",
    "UserRepository",
]
