# syntax=docker/dockerfile:1.7
# =====================================================================================
# TaskHub API — production image
#
# Three stages so the runtime image carries no build tooling:
#   builder  → compiles wheels into a virtualenv
#   runtime  → copies that virtualenv, runs as a non-root user
#   (dev target) → adds the dev extras and the source, for `docker compose` overrides
# =====================================================================================

# -------------------------------------------------------------------------- builder
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Build deps for the wheels that have no manylinux build (psycopg binary is preferred,
# but keep the compiler around so a missing wheel cannot break the build).
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Dependencies first: this layer stays cached until requirements.lock.txt changes.
COPY requirements.lock.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.lock.txt

# --------------------------------------------------------------------------- runtime
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/opt/venv/bin:$PATH" \
    APP_ENV=production

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin taskhub

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=taskhub:taskhub alembic.ini pyproject.toml ./
COPY --chown=taskhub:taskhub alembic ./alembic
COPY --chown=taskhub:taskhub app ./app
COPY --chown=taskhub:taskhub docker/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN chmod +x /usr/local/bin/entrypoint.sh

USER taskhub
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health/live || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["serve"]

# ---------------------------------------------------------------------------- dev
FROM runtime AS dev

USER root
RUN /opt/venv/bin/pip install --no-cache-dir pytest pytest-asyncio pytest-cov ruff mypy httpx
USER taskhub
CMD ["serve", "--reload"]