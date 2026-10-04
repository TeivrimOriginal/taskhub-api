# TaskHub API

> Production-grade async task & project management API built with **FastAPI**, **Python 3.12+**, **PostgreSQL** and **Docker**.
> Every piece of infrastructure runs in containers; `docker compose up` gives you a working system with auth, RBAC, caching, background jobs, metrics and tests.

[![Python](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0-D71F00?logo=sqlalchemy&logoColor=white)](https://www.sqlalchemy.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16%2B-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![Ruff](https://img.shields.io/badge/lint-ruff-261230)](https://docs.astral.sh/ruff/)
[![mypy](https://img.shields.io/badge/types-mypy%20strict-2A6DB2)](https://mypy-lang.org/)
[![Tests](https://img.shields.io/badge/tests-pytest-42A5F5?logo=pytest&logoColor=white)](https://docs.pytest.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## What was actually run

Every number below is copy-pasted from a real run, not estimated. Python 3.14.6,
PostgreSQL 17.4 on `127.0.0.1:5434`, Redis reachable on `127.0.0.1:6379`:

```
pytest                         → 280 passed, 1 skipped in 37.84s
coverage (branch=True)         → TOTAL 4441 stmts, 314 miss, 398 branch, 82 partial, 91.11%
                                 gate is 85% — reached
ruff check .                   → All checks passed!
ruff format --check .          → 95 files already formatted
mypy                           → Success: no issues found in 87 source files
alembic upgrade head           → 8 tables (identical to Base.metadata), 21 indexes
                                 citext installed; users.email udt_name = citext
alembic downgrade base         → no application tables left
smoke.py against live uvicorn  → 67 passed / 0 failed
```

`make migration-verify` runs the schema comparison end to end and rolls everything back
again. The suite stays green on a bare machine with no Redis — the Redis-only tests skip —
and gets stricter in CI, where `redis:7` and `postgres:16` are service containers.

**What is _not_ verified here:** the machine this was last run on has no Docker, so
`Dockerfile`, `docker-compose.yml` and `scripts/smoke.py` have never been executed locally
against a live container stack. CI covers them instead — the `Docker image` job builds the
production image and asserts it is non-root with a healthcheck, `Compose config` validates
the compose file — but "docker compose up gives you a working system" is still a claim this
repository has not proven end to end. It is the first thing to run on a machine with Docker.

---

## Table of contents

- [Why this project](#why-this-project)
- [Feature matrix](#feature-matrix)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Quick start](#quick-start)
- [API surface](#api-surface)
- [Authentication flow](#authentication-flow)
- [Project layout](#project-layout)
- [Database schema](#database-schema)
- [Testing](#testing)
- [Code quality](#code-quality)
- [Observability](#observability)
- [Design decisions (ADR summary)](#design-decisions-adr-summary)
- [What I would do next](#what-i-would-do-next)

---

## Why this project

Most junior FastAPI repositories are a single `main.py` with a `crud.py` that talks
straight to the database. This one is built the way a real backend is built:

- **Clean layering** — `api → services → repositories → models`. Business rules never
  live in a route handler, SQL never lives in a route handler.
- **Everything async** — `AsyncSession` + `asyncpg` + `redis.asyncio` + `httpx`, with a
  Celery worker for the slow work.
- **Typed to the last character** — `mypy --strict` passes with zero errors.
- **Tested** — 85%+ coverage gate, unit tests without a database and integration tests
  against a real PostgreSQL container.
- **Operable** — structured logs with `request_id`, Prometheus metrics, health
  (`/health/live`, `/health/ready`) and error tracking hooks.
- **Reproducible** — one `docker compose up` builds API, worker, beat, PostgreSQL, Redis
  and Nginx; Alembic migrations run automatically.

---

## Feature matrix

| Area | What is implemented |
| --- | --- |
| **Auth** | OAuth2 password flow, JWT access + refresh tokens, rotating refresh tokens with **reuse detection** (a stolen token blacklists the whole family), bcrypt password hashing, logout / logout-all |
| **RBAC** | `user` / `moderator` / `admin` roles, declarative `require_roles(...)` dependency, ownership checks per resource |
| **Domain** | Projects → Tasks → Comments, many-to-many tags, task priority/status/due dates, soft deletes, activity log |
| **Queries** | Cursor-free offset pagination with hard caps, filtering, sorting, `ILIKE` search, aggregate stats |
| **Infra** | Alembic migrations, Redis cache with invalidation on write, sliding-window rate limiting, CORS, GZip, request IDs |
| **Async jobs** | Celery: e-mail notifications on task assignment and on overdue scans, beat schedule, retries + backoff |
| **Quality** | ruff (lint + format), mypy strict, pre-commit, 85% coverage gate, GitHub Actions CI |
| **Ops** | Multi-stage Dockerfile, non-root user, healthchecks, structured JSON logs, `/metrics`, Sentry hook |

---

## Architecture

```mermaid
flowchart LR
    subgraph client["Client"]
        SW[Web / Mobile]
        CURL[curl / HTTPie]
    end

    subgraph edge["Edge"]
        NG[Nginx<br/>TLS · gzip · rate limit]
    end

    subgraph app["FastAPI application (uvicorn workers)"]
        MW1[RequestID middleware]
        MW2[Logging + Metrics middleware]
        MW3[RateLimit middleware<br/>Redis fixed window]
        R[API v1 routers<br/>validation · auth · RBAC]
        S[Service layer<br/>business rules · transactions]
        REPO[Repository layer<br/>generic CRUD + queries]
        CACHE[(Redis cache)]
        SCH[BackgroundScheduler<br/>in-process periodic jobs]
    end

    subgraph infra["Stateful services"]
        PG[(PostgreSQL 16<br/>asyncpg · SQLAlchemy 2.0)]
        RD[(Redis 7<br/>cache · broker · throttle)]
    end

    subgraph async_jobs["Async processing"]
        W[Celery worker]
        BEAT[Celery beat]
        MAIL[Mailhog / SMTP]
    end

    subgraph obs["Observability"]
        PROM[Prometheus]
        SENTRY[Sentry]
    end

    SW --> NG --> MW1 --> MW2 --> MW3 --> R
    R --> S
    S --> REPO
    S <--> CACHE
    REPO --> PG
    R --> SCH
    S -->|enqueue| RD
    RD --> W
    BEAT --> RD
    W --> REPO
    W --> MAIL
    MW2 --> PROM
    MW2 --> SENTRY
```

**Layer responsibilities**

| Layer | Allowed to know about | Rule |
| --- | --- | --- |
| `app/api` | services, schemas | no SQL, no ORM models, only HTTP concerns |
| `app/services` | repositories, models | no `Request`, no HTTP status codes |
| `app/repositories` | models, session | no business rules, no external services |
| `app/models` | SQLAlchemy only | plain data + constraints + indexes |

---

## Tech stack

| Concern | Choice | Reason |
| --- | --- | --- |
| Web framework | **FastAPI 0.115** | typed DI, automatic OpenAPI, Pydantic validation |
| Data validation | **Pydantic v2** (Rust core) | 10-50× faster than v1, strict mode, `field_validator` |
| ORM | **SQLAlchemy 2.0 async** | 2.0 typed `Mapped[]` style, `selectinload` to avoid N+1 |
| DB driver | **asyncpg** | fastest async PostgreSQL driver, native prepared statements |
| Migrations | **Alembic** | autogenerate + reviewable SQL in `versions/` |
| Cache / broker | **Redis 7** | one dependency for cache, Celery broker and rate limiting |
| Background jobs | **Celery** | industry standard, retries, beat, task routing |
| Config | **pydantic-settings** | env-driven, validated, `.env` support, 12-factor |
| Passwords | **bcrypt 4** | adaptive, battle-tested, salted by default |
| Tokens | **PyJWT 2** | `jti` + `typ` + `exp` claims, rotation and revocation |
| Logs | **structlog** | key/value JSON logs bound to `request_id` |
| Metrics | **prometheus-client** | `/metrics` endpoint, histogram of request latency |
| Tests | **pytest + pytest-asyncio + httpx** | ASGI-level integration tests, no server needed |
| Lint/format | **ruff** | lint + format in one fast binary |
| Typing | **mypy strict** | catches real bugs, not just style |

---

## Quick start

Requirements: Docker Desktop + Docker Compose v2.

```bash
git clone https://github.com/OWNER/taskhub-api.git
cd taskhub-api
cp .env.example .env          # change SECRET_KEY and passwords
docker compose up --build -d  # api, worker, beat, postgres, redis, nginx, mailpit
```

| Service | URL | Notes |
| --- | --- | --- |
| Swagger UI | <http://localhost:8000/docs> | interactive, JWT "Authorize" button |
| ReDoc | <http://localhost:8000/redoc> | reference-style docs |
| OpenAPI JSON | <http://localhost:8000/openapi.json> | consumed by Postman / codegen |
| Nginx (prod-like) | <http://localhost> | gzip, security headers, request logging |
| Mailpit | <http://localhost:8025> | every e-mail the app "sends" lands here |
| Metrics | <http://localhost:8000/metrics> | Prometheus exposition format |
| Readiness | <http://localhost:8000/health/ready> | 503 when a dependency is down or the schema is wrong |

The API exposes **28 paths / 43 operations**, documented in full at `/docs`.

Verify the stack end to end:

```bash
curl http://localhost:8000/health/ready

# register, login, create a project and a task
curl -X POST http://localhost:8000/api/v1/auth/register \
     -H 'Content-Type: application/json' \
     -d '{"email":"demo@taskhub.dev","username":"demo","password":"Str0ngPassw0rd!","full_name":"Demo User"}'

TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login-json \
     -H 'Content-Type: application/json' \
     -d '{"username":"demo@taskhub.dev","password":"Str0ngPassw0rd!"}' | jq -r .access_token)

curl -X POST http://localhost:8000/api/v1/projects \
     -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"name":"Release 1.0","description":"Ship the thing","color":"#4F46E5"}'
```

Or let the script do it — 67 assertions over the whole feature set, against a running
server:

```bash
make up
python scripts/smoke.py --base-url http://localhost:8000
```

```
=== refresh rotation and theft detection ===
  PASS  POST /auth/refresh -> 200
  PASS  re-using a consumed refresh token -> 401
  PASS  the whole token family is revoked after reuse detection
  PASS  POST /auth/logout -> 200
  PASS  refresh after logout -> 401
...
passed: 67   failed: 0   elapsed: 2.40s
```

### Without Docker (local development)

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
export POSTGRES_ASYNC_URI=postgresql+asyncpg://postgres:postgres@localhost:5432/taskhub
alembic upgrade head                                 # create the schema
uvicorn app.main:app --reload
celery -A app.core.celery_app.celery_app worker -l info   # optional, background jobs
```

### Everyday commands

The `Makefile` wraps the whole toolchain:

```bash
make help          # list all targets
make up            # docker compose up --build -d
make down          # stop containers
make logs          # tail api logs
make migrate       # alembic upgrade head
make makemigration # autogenerate a revision
make test          # pytest, prints the coverage report (needs postgres + redis)
make lint          # ruff check + ruff format --check
make format        # ruff check --fix + ruff format
make typecheck     # mypy --strict
make precommit     # run every hook over staged files
make shell         # psql inside the container
```

---

## API surface

```
GET    /health/live                      liveness probe (no dependencies touched)
GET    /health/ready                     readiness: DB, schema and Redis
GET    /metrics                          Prometheus metrics

POST   /api/v1/auth/register             create account, returns tokens
POST   /api/v1/auth/login                OAuth2 password form -> tokens
POST   /api/v1/auth/login-json           same, with a JSON body
POST   /api/v1/auth/refresh              rotate refresh token
POST   /api/v1/auth/logout               revoke the current refresh token
POST   /api/v1/auth/logout-all           revoke every session of the caller
GET    /api/v1/auth/me                   current user profile
PATCH  /api/v1/auth/me                   update own profile
POST   /api/v1/auth/change-password      password change revokes all sessions

GET    /api/v1/users                     admin/moderator: list users (paginated, filtered)
GET    /api/v1/users/{user_id}           self or staff
PATCH  /api/v1/users/{user_id}           self or admin
DELETE /api/v1/users/{user_id}           admin: soft delete
POST   /api/v1/users/{user_id}/activate  admin: restore soft-deleted user

POST   /api/v1/projects                  create project
GET    /api/v1/projects                  list: pagination, ?search=, ?is_active=
GET    /api/v1/projects/{project_id}     detail with task counters
PATCH  /api/v1/projects/{project_id}     owner or admin
DELETE /api/v1/projects/{project_id}     owner or admin
GET    /api/v1/projects/{project_id}/tasks   tasks of a project (filters: status, priority, assignee, tag)

POST   /api/v1/tasks                     create task (optionally enqueues notification)
GET    /api/v1/tasks                     list across projects with the same filter set
GET    /api/v1/tasks/{task_id}           detail with comments + tags
PATCH  /api/v1/tasks/{task_id}           change status/priority/assignee/dates
DELETE /api/v1/tasks/{task_id}           owner, project owner or admin
PATCH  /api/v1/tasks/{task_id}/complete  shortcut with completion timestamp
POST   /api/v1/tasks/{task_id}/tags      attach tag
DELETE /api/v1/tasks/{task_id}/tags/{tag_id}

GET    /api/v1/tasks/{task_id}/comments  paginated comments
POST   /api/v1/tasks/{task_id}/comments  add comment (invalidates cache)
DELETE /api/v1/comments/{comment_id}

GET    /api/v1/tags  POST /api/v1/tags   tag catalogue (create is idempotent)
GET    /api/v1/tags/popular             most used tags (staff only)
GET    /api/v1/stats/overview            per-user dashboard aggregates (?refresh=true bypasses the cache)
GET    /api/v1/stats/velocity           mean time-to-completion
```

Every list endpoint returns the same envelope:

```json
{
  "items": [ ... ],
  "total": 137,
  "page": 2,
  "size": 20,
  "pages": 7,
  "has_next": true,
  "has_prev": true
}
```

Every error returns the same envelope, plus the `X-Request-ID` header:

```json
{
  "error": {
    "code": "task_not_found",
    "message": "Task 42 does not exist",
    "details": {"task_id": 42},
    "request_id": "0f3a9c1e-..."
  }
}
```

---

## Authentication flow

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant A as FastAPI
    participant S as AuthService
    participant D as PostgreSQL
    participant W as Celery worker

    C->>A: POST /auth/login (username, password)
    A->>S: authenticate(email, password)
    S->>D: SELECT user, verify bcrypt hash
    D-->>S: user row
    S->>D: INSERT refresh_tokens (family_id, jti, expires_at)
    S-->>A: access token (15 min) + refresh token (14 days)
    A-->>C: {access_token, refresh_token, token_type}

    C->>A: GET /projects (Bearer access)
    A->>A: decode + verify signature/exp/aud/iss
    A->>D: SELECT user, check is_active
    A-->>C: 200 OK

    C->>A: POST /auth/refresh (refresh_token)
    A->>S: rotate()
    S->>D: SELECT token by jti
    alt token already used (possible theft)
        S->>D: revoke entire family_id
        S-->>C: 401 refresh_token_reused
    else valid
        S->>D: mark old as used, insert new row (same family)
        S-->>C: new access + new refresh
    end

    C->>A: POST /auth/logout
    A->>S: revoke(refresh_token)
    S->>D: UPDATE refresh_tokens SET revoked_at=now()
    S->>W: send "you were logged out" e-mail
    W-->>C: e-mail via Mailpit/SMTP
```

Security details worth reviewing:

- passwords hashed with **bcrypt**, cost 12, never logged, never returned by any schema;
- access tokens are stateless; refresh tokens live in the DB, are **rotated on every use**
  and carry a `family_id`;
- presenting an already-used refresh token revokes the whole family (reuse detection);
- every token has `jti`, `typ`, `sub`, `aud`, `iss`, `iat`, `exp`;
- logout revokes one token, `logout-all` / password change revokes every token;
- secrets come from env; `SECRET_KEY` shorter than 32 chars is rejected at startup.

---

## Project layout

```text
taskhub-api/
├── alembic/                     # migrations (env.py, versions/0001, versions/0002)
├── app/
│   ├── api/
│   │   ├── deps.py              # DI: session, current user, roles, pagination
│   │   ├── errors.py            # exception handlers -> unified error envelope
│   │   └── v1/
│   │       ├── router.py        # /api/v1 composition
│   │       └── routers/         # auth, users, projects, tasks, comments, tags, stats, health
│   ├── core/
│   │   ├── cache.py             # redis client, json cache, circuit breaker
│   │   ├── celery_app.py        # celery instance, queues, beat schedule, signals
│   │   ├── config.py            # pydantic-settings (12-factor), production guards
│   │   ├── constants.py         # roles, statuses, priorities, error codes
│   │   ├── errors.py            # domain exception hierarchy
│   │   ├── logging.py           # structlog setup, request-id binding
│   │   ├── metrics.py           # prometheus collectors
│   │   └── security.py          # jwt create/decode, bcrypt hashing
│   ├── db/
│   │   ├── base.py              # DeclarativeBase, naming convention, mixins
│   │   └── session.py           # engine, session factory, get_session
│   ├── models/                  # ORM models (User, Project, Task, Comment, Tag,
│   │                            #   RefreshToken, ActivityLog)
│   ├── repositories/            # generic BaseRepository + per-entity queries
│   ├── schemas/                 # pydantic v2 (Create / Update / Read / envelope)
│   ├── services/                # business rules + transaction boundaries
│   ├── workers/
│   │   ├── dispatch.py          # enqueue() that also works in eager mode
│   │   └── tasks/               # notifications, maintenance sweeps
│   ├── middleware/              # request id + access log, metrics, rate limit
│   ├── main.py                  # app factory, lifespan, middleware order
│   └── cli.py                   # python -m app.cli create-admin | seed-demo | show-settings
├── docker/
│   ├── entrypoint.sh            # wait for the DB, migrate, bootstrap, exec
│   ├── nginx/nginx.conf         # gzip, security headers, access log
│   ├── postgres/init.sql        # extensions + server defaults
│   └── prometheus.yml
├── tests/
│   ├── unit/                    # no database
│   ├── integration/             # real PostgreSQL (+ Redis when available)
│   └── conftest.py              # fixtures: db, client, users, tokens
├── scripts/
│   ├── smoke.py                   # 67 assertions against a *running* server
│   └── verify_migrations.py       # upgrade → diff vs models → downgrade (destructive)
├── .github/workflows/           # ci.yml (8 jobs), release.yml
├── .github/dependabot.yml
├── .pre-commit-config.yaml
├── .cz.toml                     # conventional commits
├── alembic.ini
├── docker-compose.yml           # api, worker, beat, postgres, redis, mailpit, nginx,
│                                #   + prometheus/grafana behind a profile
├── Dockerfile                   # multi-stage, non-root, HEALTHCHECK
├── Makefile                     # every command CI runs, and a few more
├── pyproject.toml               # ruff, mypy, pytest, coverage, packaging
├── requirements.lock.txt        # exact versions the image is built from
├── docs/ARCHITECTURE.md         # why the code is shaped like this
├── CONTRIBUTING.md
└── README.md
```

---

## Database schema

```mermaid
erDiagram
    USERS ||--o{ REFRESH_TOKENS : "owns"
    USERS ||--o{ PROJECTS : "creates"
    USERS ||--o{ TASKS : "assigned"
    USERS ||--o{ COMMENTS : "writes"
    USERS ||--o{ ACTIVITY_LOG : "acts"
    PROJECTS ||--o{ TASKS : "contains"
    TASKS ||--o{ COMMENTS : "has"
    TASKS ||--o{ ACTIVITY_LOG : "logs"
    TASKS }o--o{ TAGS : "labelled"

    USERS {
        uuid id PK
        citext email UK
        citext username UK
        text hashed_password
        varchar full_name
        enum role
        bool is_active
        bool is_superuser
        timestamptz created_at
        timestamptz updated_at
        timestamptz deleted_at
    }
    REFRESH_TOKENS {
        uuid id PK
        uuid user_id FK
        uuid jti UK
        uuid family_id
        text token_hash
        timestamptz expires_at
        timestamptz used_at
        timestamptz revoked_at
        varchar user_agent
        inet ip_address
    }
    PROJECTS {
        uuid id PK
        uuid owner_id FK
        varchar name
        text description
        varchar color
        bool is_active
    }
    TASKS {
        uuid id PK
        uuid project_id FK
        uuid assignee_id FK
        varchar title
        text description
        enum status
        enum priority
        timestamptz due_date
        timestamptz completed_at
        int position
        bool is_deleted
    }
    COMMENTS {
        uuid id PK
        uuid task_id FK
        uuid author_id FK
        text body
        bool is_deleted
    }
    TAGS {
        uuid id PK
        uuid created_by FK
        varchar name UK
        varchar color
    }
    ACTIVITY_LOG {
        bigserial id PK
        uuid user_id FK
        uuid task_id FK
        varchar action
        varchar entity_type
        jsonb payload
        timestamptz created_at
    }
```

Migrations are hand-reviewed SQL: `alembic/versions/0001_initial_schema.py` creates
tables, named constraints (`uq_users_email`, `ck_tasks_priority`, `fk_tasks_project_id_projects`),
and the indexes the API's filters actually use — including **partial** indexes
(`WHERE deleted_at IS NULL`), because every listing hides soft-deleted rows.
`0002_add_trigram_indexes.py` adds the GIN/`gin_trgm_ops` indexes that make
`?search=` (an `ILIKE '%term%'` query) an index scan instead of a sequential scan.

`make migration-check` runs `alembic check`, which fails if the models and the migrations
ever disagree.

---

## Testing

```bash
make test             # whole suite; prints the coverage report, enforces no gate
make gate             # whole suite + the 85% gate (needs Python >= 3.14, see below)
pytest -m unit        # fast, no database — ~1.5 s
pytest -m integration -k task
make smoke            # 67 assertions against a *running* server
```

Measured on this machine (Python 3.14, PostgreSQL 17, Redis up):

```
280 passed, 1 skipped in 37.84s
TOTAL   4441 stmts   314 miss   398 branch   82 partial   91.11%
Required test coverage of 85% reached.
```

Highlights:

- **unit** — password hashing (including the >72-byte bcrypt truncation trap), JWT
  create/decode/expiry/audience checks, refresh rotation and reuse detection, the
  pagination envelope, every schema validator, config guards (a production `SECRET_KEY`
  shorter than 32 characters refuses to boot);
- **integration** — full request cycles through ASGI (`httpx.AsyncClient`) against a real
  PostgreSQL: registration, login (form *and* JSON), rotation, theft detection with family
  revocation, logout / logout-all / password change, the RBAC matrix (user / moderator /
  admin × endpoint), pagination boundaries, filter combinations, uniqueness conflicts (409),
  not-found (404), validation errors (422 with per-field details), soft deletes and
  counter rollback, stats correctness, cache invalidation, Celery task routing, the
  idempotent overdue sweep, rate limiting;
- **fixtures** — schema created once per session and truncated per test, one HTTP client per
  identity, `asyncpg`-backed isolation rather than mocks;
- every test asserts on the response envelope, not on internal objects.

The single skip is the test that only makes sense without Redis (it asserts the
degraded path). With no Redis at all the suite skips five cache-specific tests instead, so
it is green on a bare laptop and stricter in CI where `redis:7` is a service.

### Why the coverage gate only runs on one Python version

A coverage gate is only worth having if the number behind it is real. On this code base the
two tracers `coverage.py` can choose between disagree by seven points, on the same 280
tests:

| Core | Result | `app/services/task.py` |
| --- | --- | --- |
| `sys.settrace` (C tracer, the default ≤ 3.13) | 84.12% | 56.84% |
| PEP 669 `sys.monitoring` (`sysmon`, the default on 3.14) | **91.11%** | 90.17% |

The cause is specific and worth stating precisely: **the C tracer drops the statement that
follows every `await`.** In `app/api/v1/routers/tags.py` it records line 34
(`tag = await service.create(...)`) and misses line 35 (`return TagRead.model_validate(...)`).
SQLAlchemy's async layer resumes those coroutines from a greenlet, and the tracer's
per-thread frame bookkeeping does not survive the switch. The result is nonsense on its face —
`tests/integration/test_tasks.py` at 100% while the service it exercises reads as 57% — and it
only shows up on an interpreter whose default core is the C one.

`sysmon` measures it correctly, but its `BRANCH_LEFT`/`BRANCH_RIGHT` events only exist on
Python 3.14, and this project measures branches. So the CI is split:

- `Tests (Python 3.12 / 3.13)` runs `pytest --no-cov` and answers only "does the code pass on
  every supported interpreter?";
- `Coverage gate` runs the same suite once on 3.14 with `COVERAGE_CORE=sysmon`, and a
  follow-up step re-reads `coverage debug config` to prove the tracer was the one asked for —
  a gate that quietly measures the wrong tracer is worse than no gate.

`make gate` does the same locally. If you are on Python 3.13 and want the number, `make test`
still prints the report; just do not trust it.

---

## Code quality

| Tool | Configuration | Status |
| --- | --- | --- |
| ruff | 24 rule families, `line-length=100`, per-file ignores | clean |
| ruff format | Black-compatible formatter | applied |
| mypy | `strict = true`, `warn_unreachable` | **0 errors, 85 files** |
| pre-commit | ruff, ruff-format, mypy, gitleaks, commitizen | configured |
| pytest | `branch = true`, gate at 85% on Python 3.14 | gate |
| gitleaks | secret scan on every push | CI |

```
$ make verify
lint        → All checks passed!
types       → Success: no issues found in 87 source files
gate        → 280 passed, 1 skipped; TOTAL 4441 stmts 314 miss 91.11%
              Required test coverage of 85% reached.
migrations  → No new upgrade operations detected.
OK: lint, types, tests, the coverage gate and migration state all pass
```

CI (`.github/workflows/ci.yml`) runs eight jobs, each named after what it checks:

1. `lint` — ruff check + format check
2. `types` — mypy strict
3. `secrets` — gitleaks
4. `test` — matrix on Python 3.12 / 3.13 with `postgres:16` + `redis:7` services, run with
   `--no-cov`: it answers "does the code pass on every supported interpreter?"
5. `coverage` — the same suite once on 3.14 under `COVERAGE_CORE=sysmon`, enforcing the 85%
   gate, plus a step that verifies the tracer really is the accurate one
6. `migrations` — upgrade → downgrade to base → upgrade, then `alembic check` plus an
   autogenerate drift check
7. `docker` — builds the production image and asserts it declares a `HEALTHCHECK`, does
   **not** run as root, and can import the application
8. `compose` — `docker compose config` must be valid
---

## Observability

- **Logs** — `structlog`, JSON in production, console with colours locally; every line
  carries `request_id`, `method`, `path`, `status`, `duration_ms`, `user_id`;
- **Metrics** — `http_requests_total`, `http_request_duration_seconds` (histogram),
  `db_query_duration_seconds`, `celery_task_duration_seconds`, `auth_failures_total`;
- **Health** — `/health/live` (process is up), `/health/ready` (PostgreSQL answers, its
  schema matches this build, Redis answers);
- **Tracing/errors** — optional Sentry DSN; every unhandled exception is reported with
  the request context, while the client gets a generic message.

---

## Design decisions (ADR summary)

| # | Decision | Alternatives | Why |
| --- | --- | --- | --- |
| 1 | Layered monolith (api → services → repositories) | single module, full DDD aggregates | best ratio of clarity to complexity for a service of this size; testable in isolation |
| 2 | Repository pattern instead of active-record helpers | ORM "get_or_create" shortcuts | keeps SQL and mapping in one place, services stay framework-free |
| 3 | UUID primary keys | `bigint` auto-increment | IDs are not guessable and can be created client-side; standard in distributed systems |
| 4 | `citext` for e-mail/username | `lower()` + unique index | uniqueness must be case-insensitive; `lower()` in an index is fragile |
| 5 | Rotating refresh tokens with reuse detection | long-lived refresh token | a leaked token is detected and the whole family revoked |
| 6 | Redis cache with explicit invalidation in services | TTL-only cache | TTL-only serves stale data after a write; explicit `delete_pattern` keeps it honest |
| 7 | Fixed-window rate limit in middleware | token bucket in Redis/Lua | simple, no Lua, one round trip (`INCR` + `EXPIRE`) |
| 8 | Celery for anything slow | `BackgroundTasks` | retries, scheduling, separate failure domain; `BackgroundTasks` dies with the request |
| 9 | `pydantic-settings` | `os.environ` + manual parsing | validated configuration that fails fast at startup |
| 10 | Alembic only, no `create_all` in prod | runtime `create_all` | schema changes must be reviewable, reversible and reproducible |
| 11 | Readiness verifies the *schema*, not just the socket | `SELECT 1` | a container that started before its migrations answers `SELECT 1` happily and then 503s every request; the probe has to keep that instance out of rotation |
| 12 | A `ProgrammingError` is a 500, not a 503 | one bucket for every SQLAlchemy error | "relation does not exist" is not transient — answering "please retry" is a lie that keeps a broken instance in the load balancer |
| 13 | The coverage gate runs on one interpreter, with the tracer pinned | gate on every matrix entry | `sys.settrace` loses every line after an `await` here; a gate on the wrong tracer is worse than no gate |

---

## What I would do next

- **Horizontal scale**: read replicas via `postgresql+asyncpg` secondary engine, and a
  Redis Cluster / sentinel setup instead of a single node.
- **Caching layer**: add `fastapi-cache2` with a tag-based invalidation so cache keys are
  derived from the query object instead of hand-written strings.
- **API versioning**: the `/api/v1` prefix is already in place; add `Deprecation`
  headers and a sunset policy for `/v1` before `/v2`.
- **Full-text search**: `tsvector` + GIN index for projects and tasks, exposed as
  `?q=` with `websearch_to_tsquery`.
- **Event-driven parts**: publish task events to a stream (Redis Streams / Kafka) and
  build notifications on top, instead of calling services directly.
- **SLOs**: alert rules on `http_request_duration_seconds` p95 and error budget burn.

---

## License

MIT — see [LICENSE](LICENSE).

## Author

Built as a portfolio project to demonstrate production-oriented FastAPI/Python/PostgreSQL
engineering: async programming, clean architecture, database design, testing, CI/CD and
observability.
