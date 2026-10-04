"""Pytest fixtures.

Design notes
------------
* The schema is created **once per session** with `Base.metadata.create_all`; per-test
  isolation comes from wrapping each test in a transaction that is rolled back.
  Truncating tables between tests would be simpler but much slower and would also destroy
  sequences that the pagination tests depend on.
* `bcrypt` runs with 4 rounds during tests (`TEST_BCRYPT_ROUNDS`) — 12 rounds would add
  ~250 ms per password and buy nothing.
* Redis is optional: tests that need it are skipped when it is unreachable, so the suite
  passes on a laptop without a Redis server and still covers the cache in CI.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from collections.abc import Callable

# --------------------------------------------------------------------- env
TEST_ENVIRONMENT = {
    "ENVIRONMENT": "testing",
    "DEBUG": "false",
    "LOG_LEVEL": "WARNING",
    "LOG_JSON": "false",
    "RATE_LIMIT_ENABLED": "false",
    "CACHE_ENABLED": "false",
    "CELERY_TASK_ALWAYS_EAGER": "true",
    "BCRYPT_ROUNDS": "4",
    "SECRET_KEY": "test-secret-key-not-used-anywhere-else-0123456789",
    "POSTGRES_ASYNC_URI": os.getenv(
        "TEST_POSTGRES_ASYNC_URI",
        "postgresql+asyncpg://postgres:postgres@127.0.0.1:5434/taskhub_test",
    ),
    "POSTGRES_SYNC_URI": os.getenv(
        "TEST_POSTGRES_SYNC_URI",
        "postgresql+psycopg://postgres:postgres@127.0.0.1:5434/taskhub_test",
    ),
}
os.environ.update(TEST_ENVIRONMENT)

from app.core.config import Settings, get_settings  # noqa: E402
from app.core.constants import Role, TaskPriority  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_session, get_session_factory  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Comment, Project, Tag, Task, User  # noqa: E402

TEST_PASSWORD = "T3stPassword!"


# ------------------------------------------------------------------ settings
@pytest.fixture(scope="session")
def settings() -> Settings:
    """Settings resolved with the test overrides above."""
    return get_settings()


# -------------------------------------------------------------- database
@pytest.fixture(scope="session")
def _schema(settings: Settings) -> Generator[None, None, None]:
    """Create the schema once for the whole session, then drop it.

    Deliberately synchronous: pytest-asyncio 1.x forbids session-scoped async fixtures,
    and DDL does not benefit from an event loop anyway — the *sync* DSN is used here
    precisely because the async engine belongs to the per-test fixtures.
    """
    from sqlalchemy import create_engine, text

    engine = create_engine(settings.postgres_sync_uri, future=True)
    with engine.begin() as connection:
        # Extensions the models depend on: `citext` for the case-insensitive
        # e-mail/username columns, `pg_trgm` for the GIN trigram indexes. The test
        # database must not depend on a migration having been run first.
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    Base.metadata.create_all(engine)
    try:
        yield
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
async def db_session(_schema: None) -> AsyncGenerator[AsyncSession, None]:
    """A session on a database that is emptied before each test.

    Truncation — rather than wrapping the test in a transaction that is rolled back —
    is required here because the service layer commits its own transactions: a rollback
    wrapper would be closed by the first `commit()` inside a request. `TRUNCATE ...
    CASCADE` is a single statement, so it stays fast even with the whole schema in play.
    """
    factory = get_session_factory()
    async with factory() as session:
        await _truncate_all(session)
        yield session


async def _truncate_all(session: AsyncSession) -> None:
    """Empty every application table in one round trip.

    `TRUNCATE` is transactional in PostgreSQL, so the statement must be *committed* —
    rolling back here would silently undo the cleanup and leak rows into the next test.
    """
    from sqlalchemy import text

    tables = ", ".join(
        table.name
        for table in reversed(Base.metadata.sorted_tables)
        if table.name != "alembic_version"
    )
    await session.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
    await session.commit()


# ------------------------------------------------------------------- client
async def _build_client(db_session: AsyncSession) -> AsyncClient:
    """Create an ASGI client that shares the test session/transaction.

    `get_session` is overridden so the application uses the very same session the fixture
    manages; without that the app would open its own connections and its writes would
    escape the test's isolation.
    """
    app = create_app(get_settings())

    async def _override_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_session] = _override_session
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """An unauthenticated HTTP client bound to the ASGI app."""
    http = await _build_client(db_session)
    try:
        yield http
    finally:
        await http.aclose()


@pytest.fixture
async def new_client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """A second, independent client — for tests that need two identities at once.

    A single client carries a single `Authorization` header, so a test that plays both a
    plain user and an admin needs two of them.
    """
    http = await _build_client(db_session)
    try:
        yield http
    finally:
        await http.aclose()


# ---------------------------------------------------------------- factories
@pytest.fixture
def make_user(db_session: AsyncSession) -> Callable[..., Any]:
    """Create and persist a user inside the test transaction."""

    def _factory(
        *,
        role: Role = Role.USER,
        is_active: bool = True,
        is_superuser: bool = False,
        password: str = TEST_PASSWORD,
        email: str | None = None,
        username: str | None = None,
    ) -> User:
        unique = uuid.uuid4().hex[:8]
        user = User(
            email=email or f"user_{unique}@taskhub.dev",
            username=username or f"user_{unique}",
            full_name=f"User {unique}",
            hashed_password=hash_password(password, rounds=4),
            role=role,
            is_active=is_active,
            is_superuser=is_superuser,
        )
        db_session.add(user)
        return user

    return _factory


@pytest.fixture
def flush(db_session: AsyncSession) -> Callable[[], Any]:
    """Force pending changes so generated ids are available."""

    async def _flush() -> None:
        await db_session.flush()

    return _flush


@pytest.fixture
async def user(make_user: Callable[..., Any], flush: Callable[[], Any]) -> User:
    """A plain active user."""
    created = make_user()
    await flush()
    return created


@pytest.fixture
async def staff_user(make_user: Callable[..., Any], flush: Callable[[], Any]) -> User:
    """A moderator, used for the "staff may read" rules."""
    created = make_user(role=Role.MODERATOR)
    await flush()
    return created


@pytest.fixture
async def admin_user(make_user: Callable[..., Any], flush: Callable[[], Any]) -> User:
    """An administrator with superuser powers."""
    created = make_user(role=Role.ADMIN, is_superuser=True)
    await flush()
    return created


@pytest.fixture
async def project(db_session: AsyncSession, user: User, flush: Callable[[], Any]) -> Project:
    """A project owned by `user`."""
    entity = Project(owner_id=user.id, name="Test project", color="#4F46E5")
    db_session.add(entity)
    await flush()
    return entity


@pytest.fixture
async def other_project(db_session: AsyncSession, flush: Callable[[], Any]) -> Project:
    """A project owned by somebody else — used for the permission tests."""
    owner = User(
        email=f"other_{uuid.uuid4().hex[:8]}@taskhub.dev",
        username=f"other_{uuid.uuid4().hex[:8]}",
        hashed_password=hash_password(TEST_PASSWORD, rounds=4),
    )
    db_session.add(owner)
    await flush()
    entity = Project(owner_id=owner.id, name="Foreign project")
    db_session.add(entity)
    await flush()
    return entity


@pytest.fixture
async def task(db_session: AsyncSession, project: Project, flush: Callable[[], Any]) -> Task:
    """A task inside `project`."""
    entity = Task(project_id=project.id, title="Test task", priority=TaskPriority.HIGH)
    db_session.add(entity)
    await flush()
    return entity


@pytest.fixture
async def tag(db_session: AsyncSession, user: User, flush: Callable[[], Any]) -> Tag:
    """A reusable tag."""
    entity = Tag(name=f"tag-{uuid.uuid4().hex[:8]}", created_by=user.id)
    db_session.add(entity)
    await flush()
    return entity


@pytest.fixture
async def comment(
    db_session: AsyncSession, task: Task, user: User, flush: Callable[[], Any]
) -> Comment:
    """A comment on `task` written by `user`."""
    entity = Comment(task_id=task.id, author_id=user.id, body="Looks good")
    db_session.add(entity)
    await flush()
    return entity


# ------------------------------------------------------------------- auth
def issue_token(
    user: User | uuid.UUID,
    *,
    token_type: str = "access",
    role: str = "user",
    **kwargs: Any,
) -> str:
    """Mint a token for a user (or a bare id) without going through the HTTP layer.

    Accepting a raw id matters for the permission tests: they must be able to
    authenticate as somebody *other* than the user their fixtures created.
    """
    from datetime import timedelta

    from app.core.constants import TokenType
    from app.core.security import create_token

    subject = user.id if isinstance(user, User) else user
    issued = create_token(
        subject=subject,
        token_type=TokenType(token_type),
        settings=get_settings(),
        expires_delta=kwargs.pop("expires_delta", timedelta(minutes=15)),
        role=user.role.value if isinstance(user, User) else role,
        **kwargs,
    )
    return issued.token


@pytest.fixture
def token_for() -> Callable[..., str]:
    """Raw token factory, for tests that must act as an arbitrary user id."""
    return issue_token


@pytest.fixture
def auth_headers() -> Callable[[User], dict[str, str]]:
    """Build an `Authorization` header for a user."""

    def _headers(user: User, *, token_type: str = "access", **kwargs: Any) -> dict[str, str]:
        return {"Authorization": f"Bearer {issue_token(user, token_type=token_type, **kwargs)}"}

    return _headers


@pytest.fixture
def logged_in(client: AsyncClient, user: User) -> AsyncClient:
    """A client whose requests are authenticated as `user`."""
    client.headers.update({"Authorization": f"Bearer {issue_token(user)}"})
    return client


@pytest.fixture
def as_admin(client: AsyncClient, admin_user: User) -> AsyncClient:
    client.headers.update({"Authorization": f"Bearer {issue_token(admin_user)}"})
    return client


@pytest.fixture
def as_staff(client: AsyncClient, staff_user: User) -> AsyncClient:
    client.headers.update({"Authorization": f"Bearer {issue_token(staff_user)}"})
    return client


# ------------------------------------------------------------------- time
@pytest.fixture
def in_the_past() -> Callable[[int], datetime]:
    def _factory(days: int = 1) -> datetime:
        return datetime.now(UTC) - timedelta(days=days)

    return _factory


@pytest.fixture
def in_the_future() -> Callable[[int], datetime]:
    def _factory(days: int = 1) -> datetime:
        return datetime.now(UTC) + timedelta(days=days)

    return _factory


# ----------------------------------------------------------------- celery
@pytest.fixture
def eager_celery() -> Generator[None, None, None]:
    """Run Celery tasks inline instead of pushing them to a broker."""
    from app.core.celery_app import celery_app

    previous = celery_app.conf.task_always_eager
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    try:
        yield
    finally:
        celery_app.conf.task_always_eager = previous


# ----------------------------------------------------------------- redis
@pytest.fixture(scope="session")
def redis_available(settings: Settings) -> bool:
    """`True` when a Redis server answers; used to skip cache-specific tests.

    Deliberately synchronous: pytest-asyncio 1.x does not allow a session-scoped async
    fixture, and a plain `redis.Redis` probe needs no event loop at all.
    """
    import redis as sync_redis

    try:
        client = sync_redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        return bool(client.ping())
    except Exception:
        return False


@pytest.fixture
def requires_redis(redis_available: bool) -> None:
    if not redis_available:
        pytest.skip("Redis is not reachable in this environment")
