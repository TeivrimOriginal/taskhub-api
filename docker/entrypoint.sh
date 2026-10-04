#!/usr/bin/env bash
# =====================================================================================
# Container entrypoint.
#
# Every long-running command waits for PostgreSQL first: `depends_on: service_healthy`
# covers Compose, but this script also covers `docker run` and CI, where nothing else
# guarantees an ordering.
# =====================================================================================
set -Eeuo pipefail

log() { printf '%s [entrypoint] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

wait_for_database() {
    if [[ "${RUN_MIGRATIONS:-true}" != "true" ]]; then
        return 0
    fi

    local attempts="${DB_WAIT_ATTEMPTS:-30}"
    log "waiting for PostgreSQL (max ${attempts} attempts)…"

    for attempt in $(seq 1 "${attempts}"); do
        if python - <<'PY'
import asyncio
import sys

from sqlalchemy import text

from app.db.session import get_engine, dispose_engine


async def main() -> int:
    try:
        async with get_engine().connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - the exit code is the whole answer here
        print(exc, file=sys.stderr)
        return 1
    finally:
        await dispose_engine()
    return 0


raise SystemExit(asyncio.run(main()))
PY
        then
            log "PostgreSQL is ready"
            return 0
        fi
        sleep 1
    done

    log "PostgreSQL did not become ready in time"
    return 1
}

apply_migrations() {
    if [[ "${RUN_MIGRATIONS:-true}" != "true" ]]; then
        return 0
    fi
    log "applying migrations"
    alembic upgrade head
}

bootstrap_admin() {
    if [[ "${RUN_CREATE_ADMIN:-true}" != "true" ]]; then
        return 0
    fi
    log "ensuring the bootstrap admin exists"
    python -m app.cli create-admin
}

command="${1:-serve}"
shift || true

case "${command}" in
    serve)
        wait_for_database
        apply_migrations
        bootstrap_admin
        log "starting uvicorn on ${APP_HOST:-0.0.0.0}:${APP_PORT:-8000}"
        # Logging is configured by `app.core.logging` in the lifespan hook, so uvicorn
        # itself only needs to stop installing its own handlers.
        exec uvicorn app.main:app \
            --host "${APP_HOST:-0.0.0.0}" \
            --port "${APP_PORT:-8000}" \
            --workers "${UVICORN_WORKERS:-2}" \
            --proxy-headers \
            --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-*}" \
            "$@"
        ;;
    worker)
        wait_for_database
        log "starting celery worker"
        exec celery -A app.core.celery_app:celery_app worker \
            --loglevel="${CELERY_LOG_LEVEL:-info}" \
            --concurrency="${CELERY_CONCURRENCY:-4}" \
            --queues="${CELERY_QUEUES:-default,notifications,maintenance}"
        ;;
    beat)
        wait_for_database
        log "starting celery beat"
        exec celery -A app.core.celery_app:celery_app beat \
            --loglevel="${CELERY_LOG_LEVEL:-info}" \
            --schedule="${CELERY_SCHEDULE_FILE:-/tmp/celerybeat-schedule}"
        ;;
    migrate)
        wait_for_database
        apply_migrations
        ;;
    admin)
        bootstrap_admin
        ;;
    shell)
        exec python
        ;;
    test)
        wait_for_database
        log "running the test suite"
        exec pytest "${@}"
        ;;
    *)
        exec "${command}" "$@"
        ;;
esac