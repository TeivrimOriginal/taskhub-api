# Contributing

Thanks for taking the time. This file describes how to get the project running and what a
change is expected to look like.

---

## Getting it running

```bash
git clone https://github.com/OWNER/taskhub-api.git
cd taskhub-api
cp .env.example .env
docker compose up --build -d
```

Swagger UI: <http://localhost:8000/docs>. Bootstrap admin: `admin@taskhub.dev` /
`Admin123!` (change it in `.env`).

Without Docker:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload
```

## Before you push

```bash
make verify        # lint + mypy --strict + tests + coverage gate + migration drift check
```

That is exactly what CI runs, so a green local `make verify` means a green pipeline.

## The shape of a change

* **One concern per commit.** `feat: add task reassignment`, not `update stuff`.
* **A test with the fix.** A bug fix without a regression test will be re-introduced;
  a feature without a test cannot be reviewed.
* **A migration for a schema change.** `make makemigration m="..."`, then read the
  generated file. `make migration-check` must stay silent.
* **Docstrings that say why.** The code says what; the comment should say what was
  rejected and why — a reviewer cannot reconstruct that from the diff.

## Where code goes

| Change | Home |
| --- | --- |
| new endpoint | `app/api/v1/routers/<domain>.py` + its service method |
| new query | `app/repositories/<entity>.py` |
| new column | `app/models/<entity>.py` **and** a migration |
| new background job | `app/workers/tasks/<area>.py` |
| anything a client sees | `app/schemas/` |

Business rules never go in a route handler. SQL never goes above the repository layer.

## Style

`ruff` is the single source of truth for both linting and formatting:

```bash
make format      # ruff check --fix && ruff format
make lint        # verify only
```

`mypy` runs in strict mode. If a type is genuinely unknown, `cast` with a comment is
better than `# type: ignore` — an ignore hides the next real error too.

## Tests

```bash
make test              # everything; prints the coverage report, enforces no gate
make gate              # everything + the 85% coverage gate (needs Python >= 3.14)
make test-unit         # no database required, ~1.5 s
make test-integration   # real PostgreSQL
```

* Unit tests are for logic that needs no IO: hashing, tokens, validators, envelopes.
* Integration tests drive the ASGI app with `httpx` against a real database. They assert
  on the response envelope, not on internal objects.
* Tests requiring Redis are skipped automatically when no server is reachable, so the
  suite is green on a bare laptop and stricter in CI.
* Anything that shares Redis across tests has to give itself a private key. The rate
  limiter buckets on `ratelimit:<client>:<window>`, and that bucket survives both a test
  and a whole `pytest` process — see `_limited_client()` in
  `tests/integration/test_infrastructure.py` for how the limiter tests stay hermetic.
* Skipping is a safety net, not a licence to forget. If you want the Redis-dependent
  tests to actually run locally without installing a server, `tools/redis_stub.py` starts
  a real RESP-speaking stand-in (`fakeredis.TcpFakeServer`) on port 6379 in its own
  virtualenv, so `fakeredis` stays out of the project dependencies:

  ```bash
  python -m venv .venv-redis
  .venv-redis/Scripts/pip install fakeredis      # Windows
  .venv-redis/Scripts/python tools/redis_stub.py
  pytest tests/unit/test_cache.py -v             # the skipped test now reports PASSED
  ```

### Why the gate only runs on Python 3.14

`coverage.py` picks its tracer per interpreter, and on this code base the two options
disagree by seven points on identical tests. The C tracer implements `sys.settrace` and
loses the statement after every `await`, because SQLAlchemy resumes those coroutines from
a greenlet and the tracer's frame bookkeeping does not survive the switch. PEP 669
`sys.monitoring` measures it correctly, but its branch events only exist on 3.14 — and
this project measures branches.

So `make gate` pins `COVERAGE_CORE=sysmon` and needs 3.14, and CI splits the same way:
`Tests (3.12 / 3.13)` runs `pytest --no-cov`, `Coverage gate` runs the suite once under the
tracer that can be trusted. On 3.12 or 3.13 `make test` still prints a report; do not read
a percentage off it.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/), enforced by a pre-commit
hook:

```
feat(auth): add refresh-token reuse detection
fix(cache): stop invalidating when the cache is disabled
docs(readme): explain the pagination envelope
```

## Reporting a bug

Open an issue with the request, the response body (the `request_id` from
`X-Request-ID` is what makes the logs searchable), and the steps to reproduce.