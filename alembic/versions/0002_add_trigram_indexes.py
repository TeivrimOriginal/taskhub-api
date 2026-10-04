"""add trigram indexes for search

Adds GIN indexes over the columns the API exposes to `?search=` (ILIKE). Without them
PostgreSQL has to sequential-scan the table once the data grows past a few thousand rows,
which is exactly the query the dashboard issues on every page load.

`pg_trgm` is created in `alembic/env.py` (and in `docker/postgres/init.sql` for a bare
database), so this migration only has to build the indexes.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (index name, table, columns)
_TRIGRAM_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ix_projects_name_trgm", "projects", ("name",)),
    ("ix_tasks_title_trgm", "tasks", ("title",)),
    ("ix_comments_body_trgm", "comments", ("body",)),
)


def upgrade() -> None:
    """Build the trigram indexes."""
    for _name, _table, _columns in _TRIGRAM_INDEXES:
        op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))

    for name, table, columns in _TRIGRAM_INDEXES:
        column_list = ", ".join(columns)
        # Values come from the constant above, never from user input.
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {name} ON {table} USING gin ({column_list} gin_trgm_ops)"
        )

    # The plain b-tree on `projects.name` is superseded by the trigram index: the only
    # query that uses it is `ILIKE '%term%'`, which a b-tree cannot serve.
    op.execute("DROP INDEX IF EXISTS ix_projects_name")


def downgrade() -> None:
    """Drop the trigram indexes and restore the b-tree they replaced."""
    for name, _table, _columns in reversed(_TRIGRAM_INDEXES):
        op.execute(f"DROP INDEX IF EXISTS {name}")
    op.execute("CREATE INDEX IF NOT EXISTS ix_projects_name ON projects (name)")
