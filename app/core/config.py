"""Application configuration.

Twelve-factor style: every environment-specific value comes from the environment
(or a `.env` file), is validated by pydantic and fails fast at import time.
No module outside this one is allowed to read `os.environ`.
"""

from __future__ import annotations

import json
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent

#: A signing key shorter than this is not a secret; production refuses to start.
MIN_SECRET_LENGTH = 32


class Environment(StrEnum):
    """Deployment environment."""

    LOCAL = "local"
    TESTING = "testing"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Validated application settings.

    Attributes are grouped the same way `.env.example` is, so a reader can map a
    variable to its home in one jump.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ app
    app_name: str = "TaskHub API"
    environment: Environment = Environment.LOCAL
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"
    log_json: bool = False

    # ------------------------------------------------------------- security
    secret_key: SecretStr = SecretStr("insecure-development-key-do-not-use-in-production")
    jwt_algorithm: str = "HS256"
    jwt_audience: str = "taskhub-api"
    jwt_issuer: str = "taskhub-api"
    access_token_expire_minutes: int = Field(default=30, ge=1, le=1440)
    refresh_token_expire_days: int = Field(default=14, ge=1, le=365)
    bcrypt_rounds: int = Field(default=12, ge=4, le=16)

    # ----------------------------------------------------------- postgresql
    postgres_user: str = "taskhub"
    postgres_password: SecretStr = SecretStr("taskhub")
    postgres_server: str = "postgres"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = "taskhub"
    postgres_async_uri: str = ""
    postgres_sync_uri: str = ""
    db_echo: bool = False
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=20, ge=0, le=100)
    db_pool_timeout_seconds: int = Field(default=30, ge=1, le=300)
    db_pool_recycle_seconds: int = Field(default=1800, ge=60)

    # --------------------------------------------------------------- redis
    redis_host: str = "redis"
    redis_port: int = Field(default=6379, ge=1, le=65535)
    redis_password: SecretStr | None = None
    redis_url: str = "redis://redis:6379/0"
    redis_cache_url: str = "redis://redis:6379/1"

    # -------------------------------------------------------- cors / hosts
    cors_origins: list[str] = ["http://localhost:3000"]
    allowed_hosts: list[str] = ["*"]

    # ------------------------------------------------------- rate limiting
    rate_limit_enabled: bool = True
    rate_limit_requests: int = Field(default=100, ge=1, le=100_000)
    rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)

    # --------------------------------------------------------------- cache
    cache_enabled: bool = True
    cache_ttl_seconds: int = Field(default=60, ge=1, le=86400)

    # -------------------------------------------------------------- celery
    celery_broker_url: str = "redis://redis:6379/2"
    celery_result_backend: str = "redis://redis:6379/2"
    celery_task_always_eager: bool = False

    # ---------------------------------------------------------------- smtp
    smtp_host: str = "mailpit"
    smtp_port: int = Field(default=1025, ge=1, le=65535)
    smtp_user: SecretStr | None = None
    smtp_password: SecretStr | None = None
    smtp_timeout_seconds: int = Field(default=10, ge=1, le=120)
    smtp_from: str = "taskhub@localhost"
    frontend_base_url: str = "http://localhost:3000"

    # ------------------------------------------------------ observability
    sentry_dsn: str = ""
    prometheus_enabled: bool = True

    # ------------------------------------------------------ bootstrap admin
    first_superuser_email: str = "admin@taskhub.dev"
    first_superuser_password: SecretStr = SecretStr("Admin123!")
    first_superuser_name: str = "TaskHub Admin"

    # ------------------------------------------------------------ validators
    @field_validator("cors_origins", "allowed_hosts", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept both `["a","b"]` (JSON) and `a,b` (plain env var).

        The JSON form has to be parsed explicitly. `pydantic-settings` decodes a JSON
        value on its own when the list comes from an environment variable, but this
        validator also sees values passed straight to `Settings(...)` — and there a
        JSON string stayed a string, so `Settings(cors_origins='["http://a"]')` died
        with a type error instead of returning the list the docstring promised.
        """
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                try:
                    return json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"expected a JSON list or a comma-separated string, got {value!r}"
                    ) from exc
            return [item.strip() for item in text.split(",") if item.strip()]
        return value

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}, got {value!r}")
        return upper

    @model_validator(mode="after")
    def _guard_production_secrets(self) -> Settings:
        """A weak signing key in production is a vulnerability, not a nitpick."""
        if self.is_production:
            key = self.secret_key.get_secret_value()
            if len(key) < MIN_SECRET_LENGTH or key.startswith("insecure"):
                raise ValueError(
                    f"SECRET_KEY must be at least {MIN_SECRET_LENGTH} characters long "
                    "and unique in production"
                )
            if self.debug:
                raise ValueError("DEBUG must be false in production")
        return self

    @model_validator(mode="after")
    def _build_database_urls(self) -> Settings:
        """Compose DSNs from parts when they were not provided explicitly."""
        if not self.postgres_async_uri:
            password = self.postgres_password.get_secret_value()
            object.__setattr__(
                self,
                "postgres_async_uri",
                (
                    f"postgresql+asyncpg://{self.postgres_user}:{password}"
                    f"@{self.postgres_server}:{self.postgres_port}/{self.postgres_db}"
                ),
            )
        if not self.postgres_sync_uri:
            password = self.postgres_password.get_secret_value()
            object.__setattr__(
                self,
                "postgres_sync_uri",
                (
                    f"postgresql+psycopg://{self.postgres_user}:{password}"
                    f"@{self.postgres_server}:{self.postgres_port}/{self.postgres_db}"
                ),
            )
        return self

    # ------------------------------------------------------------ properties
    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION

    @property
    def is_testing(self) -> bool:
        return self.environment is Environment.TESTING

    @property
    def log_config_dict(self) -> dict[str, bool | str]:
        return {"level": self.log_level, "json": self.log_json}

    def masked_async_dsn(self) -> str:
        """DSN with the password replaced by `***` — safe for logs and errors."""
        return _mask_password(self.postgres_async_uri)

    def dsn_for(self, uri: str) -> str:
        """Return `uri` with the password replaced by `***`."""
        return _mask_password(uri)


def _mask_password(dsn: str) -> str:
    if "@" not in dsn or "://" not in dsn:
        return dsn
    scheme, rest = dsn.split("://", maxsplit=1)
    credentials, host = rest.rsplit("@", maxsplit=1)
    if ":" not in credentials:
        return dsn
    user, _password = credentials.split(":", maxsplit=1)
    return f"{scheme}://{user}:***@{host}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor so the environment is parsed exactly once per process."""
    return Settings()


settings = get_settings()
