"""How a database failure becomes an HTTP response.

The distinction this file exists to protect: a *connection* problem is worth retrying, a
*schema* problem is not. Reporting the second as `503 please retry` keeps a broken
instance in the load balancer and tells the caller to do something that cannot help.

The exceptions are injected rather than provoked. Asking PostgreSQL for a relation that
does not exist would mean dropping a table the rest of the suite depends on, and mocking
the handler directly would test the handler instead of the routing that reaches it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError

from app.services.project import ProjectService

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.models.user import User

pytestmark = pytest.mark.integration


class _FakeDriverError(Exception):
    """Stands in for the driver's exception, including its SQLSTATE."""

    def __init__(self, sqlstate: str | None) -> None:
        super().__init__('relation "users" does not exist')
        self.sqlstate = sqlstate


def _dbapi_error(error_type: type[DBAPIError], sqlstate: str | None = None) -> DBAPIError:
    """Build the SQLAlchemy wrapper the driver would have raised."""
    statement = "SELECT * FROM users"
    return error_type(statement, {}, _FakeDriverError(sqlstate))


def _raising(error: BaseException) -> Callable[..., Any]:
    async def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise error

    return boom


class TestDatabaseErrorTranslation:
    async def test_a_missing_table_is_not_reported_as_retryable(
        self,
        logged_in: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """This is what a container that skipped `alembic upgrade head` used to answer."""
        monkeypatch.setattr(
            ProjectService,
            "list_for_user",
            _raising(_dbapi_error(ProgrammingError, "42P01")),
        )

        response = await logged_in.get("/api/v1/projects")

        assert response.status_code == 500, response.text
        error = response.json()["error"]
        assert error["code"] == "internal_error"
        assert "migration" in error["message"]
        # 42P01 is `undefined_table` — an ANSI code, not a leak.
        assert error["details"] == {"sqlstate": "42P01"}

    async def test_a_connection_failure_stays_retryable(
        self,
        logged_in: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            ProjectService,
            "list_for_user",
            _raising(_dbapi_error(OperationalError, "08006")),
        )

        response = await logged_in.get("/api/v1/projects")

        assert response.status_code == 503
        error = response.json()["error"]
        assert error["code"] == "service_unavailable"
        assert "retry" in error["message"]

    async def test_the_sql_is_never_echoed_to_the_caller(
        self,
        logged_in: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A 500 body ends up in logs and dashboards; it must not carry the statement."""
        monkeypatch.setattr(
            ProjectService,
            "list_for_user",
            _raising(_dbapi_error(ProgrammingError, "42703")),
        )

        body = (await logged_in.get("/api/v1/projects")).text

        assert "SELECT * FROM users" not in body
        assert "does not exist" not in body
        # 42703 is `undefined_column`, so the sqlstate survives while the SQL does not.
        assert "42703" in body

    async def test_the_error_still_carries_the_request_id(
        self,
        logged_in: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        user: User,
    ) -> None:
        monkeypatch.setattr(
            ProjectService,
            "list_for_user",
            _raising(_dbapi_error(ProgrammingError, "42P01")),
        )

        response = await logged_in.get("/api/v1/projects")

        assert response.headers["X-Request-ID"]
        assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


class TestReadinessProtectsAgainstThis:
    async def test_an_unmigrated_instance_is_taken_out_of_rotation(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Readiness must fail on a missing schema, not just on a dead socket."""
        from app.api.v1.routers import health

        async def nothing_missing(_session: Any) -> list[str]:
            return ["users", "tasks"]

        monkeypatch.setattr(health, "missing_tables", nothing_missing)

        response = await client.get("/health/ready")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "unavailable"
        assert body["checks"]["database"] is True
        assert body["checks"]["schema"] is False
        assert body["missing_tables"] == ["users", "tasks"]

    async def test_a_probe_that_cannot_check_does_not_report_ready(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A probe that raises would surface as a 500 and hide the real cause."""
        from app.api.v1.routers import health

        async def broken(_session: Any) -> list[str]:
            raise OperationalError("SELECT ...", {}, _FakeDriverError("08006"))

        monkeypatch.setattr(health, "missing_tables", broken)

        response = await client.get("/health/ready")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "unavailable"
        assert body["checks"]["schema"] is False
        assert "detail" in body
