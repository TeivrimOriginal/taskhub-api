"""Dashboard statistics schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class StatusBreakdown(BaseModel):
    """How many tasks sit in each status."""

    model_config = ConfigDict(extra="forbid")

    todo: int = 0
    in_progress: int = 0
    in_review: int = 0
    done: int = 0
    cancelled: int = 0

    @property
    def total(self) -> int:
        return self.todo + self.in_progress + self.in_review + self.done + self.cancelled


class PriorityBreakdown(BaseModel):
    """How many tasks sit in each priority."""

    model_config = ConfigDict(extra="forbid")

    low: int = 0
    medium: int = 0
    high: int = 0
    urgent: int = 0

    @property
    def total(self) -> int:
        return self.low + self.medium + self.high + self.urgent


class StatsOverview(BaseModel):
    """Aggregated dashboard payload for one user."""

    model_config = ConfigDict(extra="forbid")

    projects_total: int = 0
    tasks_total: int = 0
    tasks_done: int = 0
    tasks_overdue: int = 0
    comments_total: int = 0
    completion_rate: float = 0.0
    by_status: StatusBreakdown = Field(default_factory=StatusBreakdown)
    by_priority: PriorityBreakdown = Field(default_factory=PriorityBreakdown)
    generated_at: datetime
