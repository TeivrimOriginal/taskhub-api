"""Configuration loading, secret masking and environment guards."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Environment, Settings, _mask_password

pytestmark = pytest.mark.unit

PRODUCTION_KEY = "a-perfectly-fine-production-secret-key-0123456789"


def build(**overrides: Any) -> Settings:
    """Build a `Settings` instance from the test baseline plus explicit overrides.

    Everything the test asserts is passed explicitly: a unit test must not depend on the
    ambient environment, and `conftest.py` overrides half of these variables.
    """
    baseline: dict[str, Any] = {
        "environment": "testing",
        "secret_key": "a-sufficiently-long-test-secret-key-0123456789",
    }
    return Settings(**{**baseline, **overrides})


class TestSettings:
    def test_defaults_are_usable(self) -> None:
        settings = build(rate_limit_enabled=True, access_token_expire_minutes=30)
        assert settings.access_token_expire_minutes == 30
        assert settings.rate_limit_enabled is True
        assert settings.environment is Environment.TESTING

    def test_builds_the_async_dsn_from_parts(self) -> None:
        settings = build(
            postgres_user="taskhub",
            postgres_password="secret",
            postgres_server="db",
            postgres_port=5433,
            postgres_db="app",
            postgres_async_uri="",
            postgres_sync_uri="",
        )
        assert settings.postgres_async_uri == "postgresql+asyncpg://taskhub:secret@db:5433/app"
        assert settings.postgres_sync_uri.startswith("postgresql+psycopg://")

    def test_explicit_dsn_wins_over_parts(self) -> None:
        settings = build(postgres_async_uri="postgresql+asyncpg://u:p@host:5432/other")
        assert settings.postgres_async_uri.endswith("/other")

    def test_cors_origins_accept_plain_csv(self) -> None:
        settings = build(cors_origins="http://a.dev, http://b.dev")
        assert settings.cors_origins == ["http://a.dev", "http://b.dev"]

    def test_cors_origins_accept_json(self) -> None:
        settings = build(cors_origins='["http://a.dev"]')
        assert settings.cors_origins == ["http://a.dev"]

    def test_log_level_is_normalised(self) -> None:
        assert build(log_level="debug").log_level == "DEBUG"

    def test_rejects_an_unknown_log_level(self) -> None:
        with pytest.raises(ValidationError):
            build(log_level="chatty")

    @pytest.mark.parametrize(
        "field",
        ["access_token_expire_minutes", "refresh_token_expire_days", "bcrypt_rounds"],
    )
    def test_rejects_out_of_range_numbers(self, field: str) -> None:
        with pytest.raises(ValidationError):
            build(**{field: 0})

    def test_rejects_an_unreachable_rate_limit_window(self) -> None:
        with pytest.raises(ValidationError):
            build(rate_limit_window_seconds=0)

    def test_production_refuses_a_short_secret(self) -> None:
        with pytest.raises(ValidationError, match="SECRET_KEY"):
            build(environment="production", secret_key="short")

    def test_production_refuses_the_placeholder_secret(self) -> None:
        placeholder = Settings.model_fields["secret_key"].default
        with pytest.raises(ValidationError, match="SECRET_KEY"):
            build(environment="production", secret_key=placeholder)

    def test_production_refuses_debug(self) -> None:
        with pytest.raises(ValidationError, match="DEBUG"):
            build(environment="production", debug=True, secret_key=PRODUCTION_KEY)

    def test_production_accepts_a_real_configuration(self) -> None:
        settings = build(environment="production", debug=False, secret_key=PRODUCTION_KEY)
        assert settings.is_production is True

    def test_local_accepts_the_placeholder_secret(self) -> None:
        """Outside production the placeholder is allowed — it is a dev convenience."""
        default = Settings.model_fields["secret_key"].default
        placeholder = default.get_secret_value() if isinstance(default, SecretStr) else str(default)
        assert placeholder.startswith("insecure")
        assert build(environment="local", secret_key=placeholder).is_production is False


class TestMasking:
    @pytest.mark.parametrize(
        ("dsn", "expected"),
        [
            (
                "postgresql+asyncpg://user:hunter2@db:5432/app",
                "postgresql+asyncpg://user:***@db:5432/app",
            ),
            ("redis://:secret@cache:6379/0", "redis://:***@cache:6379/0"),
            ("redis://cache:6379/0", "redis://cache:6379/0"),
            ("not-a-dsn", "not-a-dsn"),
        ],
    )
    def test_passwords_never_leak(self, dsn: str, expected: str) -> None:
        assert _mask_password(dsn) == expected

    def test_masked_dsn_is_usable_for_logs(self) -> None:
        settings = build(
            postgres_user="u",
            postgres_password="hunter2",
            postgres_server="db",
            postgres_port=5432,
            postgres_db="app",
            postgres_async_uri="",
        )
        masked = settings.masked_async_dsn()
        assert "hunter2" not in masked
        assert "***" in masked
