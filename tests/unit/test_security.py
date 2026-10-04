"""Password hashing, token creation/decoding and hash fingerprints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Settings
from app.core.constants import TokenType
from app.core.errors import InvalidTokenError
from app.core.security import (
    create_token,
    decode_token,
    hash_password,
    hash_token,
    needs_rehash,
    verify_password,
)

pytestmark = pytest.mark.unit

PASSWORD = "Str0ngPassw0rd!"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="testing",
        secret_key="unit-test-secret-key-that-is-long-enough-0123456789",
        access_token_expire_minutes=15,
        refresh_token_expire_days=7,
    )


class TestPasswordHashing:
    def test_hash_is_not_the_plain_password(self) -> None:
        hashed = hash_password(PASSWORD, rounds=4)
        assert hashed != PASSWORD
        assert hashed.startswith("$2b$")

    def test_hash_is_salted(self) -> None:
        assert hash_password(PASSWORD, rounds=4) != hash_password(PASSWORD, rounds=4)

    def test_verify_accepts_the_right_password(self) -> None:
        hashed = hash_password(PASSWORD, rounds=4)
        assert verify_password(PASSWORD, hashed) is True

    @pytest.mark.parametrize(
        "candidate",
        ["", "wrong", "Str0ngPassw0rd", "Str0ngPassw0rd! ", PASSWORD.upper()],
    )
    def test_verify_rejects_anything_else(self, candidate: str) -> None:
        hashed = hash_password(PASSWORD, rounds=4)
        assert verify_password(candidate, hashed) is False

    def test_verify_survives_a_corrupt_hash(self) -> None:
        assert verify_password(PASSWORD, "not-a-bcrypt-hash") is False
        assert verify_password(PASSWORD, "") is False

    def test_longer_than_72_bytes_is_not_truncated(self) -> None:
        """bcrypt ignores bytes past 72; the SHA-256 pre-hash keeps them significant."""
        base = "a" * 72
        hashed = hash_password(base + "tail-1", rounds=4)
        assert verify_password(base + "tail-1", hashed) is True
        assert verify_password(base + "tail-2", hashed) is False

    def test_needs_rehash_detects_a_weak_cost(self) -> None:
        weak = hash_password(PASSWORD, rounds=4)
        strong = hash_password(PASSWORD, rounds=6)
        assert needs_rehash(weak, rounds=12) is True
        assert needs_rehash(strong, rounds=6) is False

    def test_needs_rehash_treats_garbage_as_stale(self) -> None:
        assert needs_rehash("garbage", rounds=12) is True

    def test_token_fingerprint_is_stable_and_opaque(self) -> None:
        first = hash_token("abc.def.ghi")
        assert first == hash_token("abc.def.ghi")
        assert len(first) == 64
        assert "abc" not in first


class TestTokenCreation:
    def test_contains_the_expected_claims(self, settings: Settings) -> None:
        subject = uuid.uuid4()
        issued = create_token(subject=subject, token_type=TokenType.ACCESS, settings=settings)

        payload = decode_token(issued.token, settings=settings, expected_type=TokenType.ACCESS)
        assert payload.subject == str(subject)
        assert payload.user_id == subject
        assert payload.token_type is TokenType.ACCESS
        assert payload.jti == issued.jti
        assert payload.expires_at > datetime.now(UTC)

    def test_access_and_refresh_tokens_differ(self, settings: Settings) -> None:
        subject = uuid.uuid4()
        access = create_token(subject=subject, token_type=TokenType.ACCESS, settings=settings)
        refresh = create_token(subject=subject, token_type=TokenType.REFRESH, settings=settings)
        assert access.token != refresh.token
        assert access.jti != refresh.jti

    def test_refresh_token_carries_the_family(self, settings: Settings) -> None:
        family = uuid.uuid4()
        issued = create_token(
            subject=uuid.uuid4(),
            token_type=TokenType.REFRESH,
            settings=settings,
            family_id=family,
        )
        payload = decode_token(issued.token, settings=settings, expected_type=TokenType.REFRESH)
        assert payload.family_id == str(family)

    def test_every_token_gets_a_unique_jti(self, settings: Settings) -> None:
        ids = {
            create_token(subject=uuid.uuid4(), token_type=TokenType.ACCESS, settings=settings).jti
            for _ in range(25)
        }
        assert len(ids) == 25

    def test_default_lifetimes_come_from_settings(self, settings: Settings) -> None:
        access = decode_token(
            create_token(
                subject=uuid.uuid4(), token_type=TokenType.ACCESS, settings=settings
            ).token,
            settings=settings,
        )
        refresh = decode_token(
            create_token(
                subject=uuid.uuid4(), token_type=TokenType.REFRESH, settings=settings
            ).token,
            settings=settings,
        )
        assert access.expires_at < refresh.expires_at


class TestTokenDecoding:
    def test_rejects_a_tampered_signature(self, settings: Settings) -> None:
        issued = create_token(subject=uuid.uuid4(), token_type=TokenType.ACCESS, settings=settings)
        other = Settings(
            environment="testing",
            secret_key="a-completely-different-secret-key-0123456789",
        )
        with pytest.raises(InvalidTokenError):
            decode_token(issued.token, settings=other)

    def test_rejects_garbage(self, settings: Settings) -> None:
        for candidate in ("", "abc", "a.b.c", "..."):
            with pytest.raises(InvalidTokenError):
                decode_token(candidate, settings=settings)

    def test_rejects_the_wrong_token_type(self, settings: Settings) -> None:
        issued = create_token(subject=uuid.uuid4(), token_type=TokenType.ACCESS, settings=settings)
        with pytest.raises(InvalidTokenError):
            decode_token(issued.token, settings=settings, expected_type=TokenType.REFRESH)

    def test_rejects_an_expired_token(self, settings: Settings) -> None:
        issued = create_token(
            subject=uuid.uuid4(),
            token_type=TokenType.ACCESS,
            settings=settings,
            expires_delta=timedelta(seconds=-5),
        )
        with pytest.raises(InvalidTokenError):
            decode_token(issued.token, settings=settings)

    def test_rejects_a_non_uuid_subject(self, settings: Settings) -> None:
        """A token whose `sub` is not a UUID must never resolve to a user."""
        import jwt

        now = datetime.now(UTC)
        forged = jwt.encode(
            {
                "sub": "not-a-uuid",
                "typ": "access",
                "jti": str(uuid.uuid4()),
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(minutes=5)).timestamp()),
                "aud": settings.jwt_audience,
                "iss": settings.jwt_issuer,
            },
            settings.secret_key.get_secret_value(),
            algorithm=settings.jwt_algorithm,
        )

        with pytest.raises(InvalidTokenError, match="not a valid identifier"):
            decode_token(forged, settings=settings, expected_type=TokenType.ACCESS)
