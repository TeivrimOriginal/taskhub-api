"""Password hashing and JWT handling.

Deliberately dependency-injection friendly: every function takes the settings it
needs, so tests can hash with 4 rounds instead of 12 and stay fast.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import bcrypt
import jwt
from jwt.exceptions import InvalidTokenError

from app.core.config import Settings
from app.core.constants import TokenType
from app.core.errors import InvalidTokenError as AppInvalidTokenError

TOKEN_TYPE_CLAIM: Final = "typ"  # noqa: S105 - a claim name, not a secret
TOKEN_ID_CLAIM: Final = "jti"  # noqa: S105 - a claim name, not a secret
TOKEN_FAMILY_CLAIM: Final = "fam"  # noqa: S105 - a claim name, not a secret


# --------------------------------------------------------------------- hashing
def hash_password(password: str, *, rounds: int = 12) -> str:
    """Hash a plain password with bcrypt.

    bcrypt silently truncates at 72 bytes, so long passwords are pre-hashed with
    SHA-256 first — otherwise two passwords sharing the first 72 characters would
    be interchangeable.
    """
    salted = _prehash(password)
    return bcrypt.hashpw(salted, bcrypt.gensalt(rounds=rounds)).decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Constant-time verification of a plain password against a bcrypt hash."""
    if not hashed_password:
        return False
    try:
        return bcrypt.checkpw(_prehash(plain_password), hashed_password.encode())
    except (ValueError, TypeError):
        # Malformed hash in the database — treat as "does not match", never crash.
        return False


def _prehash(password: str) -> bytes:
    return hashlib.sha256(password.encode()).digest()


def needs_rehash(hashed_password: str, *, rounds: int = 12) -> bool:
    """True when a stored hash uses a weaker cost factor than the current setting."""
    try:
        cost = int(hashed_password.split("$")[2])
    except (IndexError, ValueError):
        return True
    return cost < rounds


def hash_token(token: str) -> str:
    """One-way fingerprint of a token, safe to store in a `token_hash` column."""
    return hashlib.sha256(token.encode()).hexdigest()


# ------------------------------------------------------------------------ JWT
@dataclass(frozen=True, slots=True)
class TokenPayload:
    """Decoded and validated token claims."""

    subject: str
    token_type: TokenType
    jti: str
    expires_at: datetime
    issued_at: datetime
    family_id: str | None = None
    role: str | None = None

    @property
    def user_id(self) -> uuid.UUID:
        return uuid.UUID(self.subject)


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """A freshly signed token plus the claims the caller has to persist."""

    token: str
    jti: str
    expires_at: datetime
    issued_at: datetime
    family_id: uuid.UUID | None = None


def create_token(
    *,
    subject: str | uuid.UUID,
    token_type: TokenType,
    settings: Settings,
    expires_delta: timedelta | None = None,
    family_id: str | uuid.UUID | None = None,
    role: str | None = None,
    additional_claims: dict[str, Any] | None = None,
) -> IssuedToken:
    """Build a signed JWT with `sub`, `typ`, `jti`, `aud`, `iss`, `iat`, `exp`.

    Returns the encoded token together with `jti`/`expires_at`, because the caller
    persists those columns — decoding the token back would be wasteful and could not
    recover them reliably after a key rotation.
    """
    now = datetime.now(UTC)
    if expires_delta is None:
        expires_delta = (
            timedelta(minutes=settings.access_token_expire_minutes)
            if token_type is TokenType.ACCESS
            else timedelta(days=settings.refresh_token_expire_days)
        )
    expires_at = now + expires_delta
    jti = str(uuid.uuid4())

    claims: dict[str, Any] = {
        "sub": str(subject),
        TOKEN_TYPE_CLAIM: token_type.value,
        TOKEN_ID_CLAIM: jti,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "aud": settings.jwt_audience,
        "iss": settings.jwt_issuer,
    }
    if family_id is not None:
        claims[TOKEN_FAMILY_CLAIM] = str(family_id)
    if role is not None:
        claims["role"] = role
    if additional_claims:
        claims.update(additional_claims)

    encoded = jwt.encode(
        claims,
        settings.secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    return IssuedToken(
        token=encoded,
        jti=jti,
        expires_at=expires_at,
        issued_at=now,
        family_id=uuid.UUID(str(family_id)) if family_id is not None else None,
    )


def decode_token(
    token: str, *, settings: Settings, expected_type: TokenType | None = None
) -> TokenPayload:
    """Decode and fully validate a JWT.

    Raises `AppInvalidTokenError` for every failure mode (bad signature, expired,
    wrong audience, wrong `typ`) so callers never have to import `jwt` exceptions.
    """
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", TOKEN_TYPE_CLAIM]},
        )
    except InvalidTokenError as exc:
        raise AppInvalidTokenError(str(exc)) from exc

    raw_type = claims.get(TOKEN_TYPE_CLAIM)
    token_type = TokenType(raw_type) if raw_type in set(TokenType) else TokenType.ACCESS

    if expected_type is not None and token_type is not expected_type:
        raise AppInvalidTokenError(
            f"Expected a {expected_type.value} token, got {token_type.value}"
        )

    raw_sub = str(claims["sub"])
    try:
        uuid.UUID(raw_sub)
    except ValueError as exc:
        raise AppInvalidTokenError("Token subject is not a valid identifier") from exc

    family = claims.get(TOKEN_FAMILY_CLAIM)
    role = claims.get("role")

    return TokenPayload(
        subject=raw_sub,
        token_type=token_type,
        jti=str(claims.get(TOKEN_ID_CLAIM, "")),
        expires_at=datetime.fromtimestamp(int(claims["exp"]), tz=UTC),
        issued_at=datetime.fromtimestamp(int(claims["iat"]), tz=UTC),
        family_id=str(family) if family is not None else None,
        role=str(role) if role is not None else None,
    )
