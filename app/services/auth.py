"""Authentication service: registration, login, token rotation, revocation.

This is the security-critical part of the application, so the rules are explicit:

* passwords are compared with bcrypt and never leave the process;
* an access token is stateless and short-lived;
* a refresh token is stored, rotated on every use and bound to a login *family*;
* presenting a consumed refresh token revokes the whole family (theft detection);
* every failure returns the same generic message so the API cannot be used to
  enumerate e-mail addresses.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import invalidate, make_key
from app.core.config import Settings
from app.core.constants import CACHE_PREFIX, TokenType
from app.core.errors import (
    InactiveUserError,
    InvalidCredentialsError,
    RefreshTokenReuseError,
    UserAlreadyExistsError,
    UserNotFoundError,
)
from app.core.logging import get_logger
from app.core.security import (
    create_token,
    decode_token,
    hash_password,
    hash_token,
    needs_rehash,
    verify_password,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.repositories.refresh_token import RefreshTokenRepository
from app.repositories.user import UserRepository
from app.schemas.auth import TokenPair
from app.schemas.user import PasswordChange, UserCreate

logger = get_logger(__name__)


class AuthService:
    """Owns the authentication state machine."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.users = UserRepository(session)
        self.tokens = RefreshTokenRepository(session)

    # ------------------------------------------------------------ registration
    async def register(self, payload: UserCreate) -> User:
        """Create an account and return it (without tokens — see `login`)."""
        email = payload.email.strip().lower()
        username = payload.username.strip().lower()

        if await self.users.email_taken(email):
            raise UserAlreadyExistsError("A user with this e-mail already exists")
        if await self.users.username_taken(username):
            raise UserAlreadyExistsError("A user with this username already exists")

        user = User(
            email=email,
            username=username,
            hashed_password=hash_password(payload.password, rounds=self.settings.bcrypt_rounds),
            full_name=payload.full_name,
        )
        await self.users.insert(user)
        await self.session.commit()
        await self.session.refresh(user)
        logger.info("user_registered", user_id=str(user.id), username=user.username)
        return user

    # ------------------------------------------------------------------- login
    async def login(
        self,
        *,
        identifier: str,
        password: str,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> TokenPair:
        """Verify credentials and issue a fresh token pair."""
        user = await self.users.get_by_email_or_username(identifier)
        # Always run a hash comparison, even for unknown users, so response time does
        # not reveal whether the account exists.
        stored_hash = user.hashed_password if user else "$2b$12$" + "." * 53
        password_ok = verify_password(password, stored_hash)

        if user is None or not password_ok:
            logger.warning("login_failed", identifier=identifier[:64], reason="bad_credentials")
            raise InvalidCredentialsError
        if not user.is_active or user.deleted_at is not None:
            logger.warning("login_failed", user_id=str(user.id), reason="inactive")
            raise InactiveUserError

        await self.users.touch_login(user)
        await self.maybe_upgrade_password_hash(user, plain_password=password)
        tokens = await self._issue_token_pair(
            user=user,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        await self.session.commit()
        await self.session.refresh(user)
        logger.info("login_succeeded", user_id=str(user.id))
        return tokens

    async def authenticate(self, *, access_token: str) -> User:
        """Resolve the bearer token into an active user, or raise."""
        payload = decode_token(
            access_token,
            settings=self.settings,
            expected_type=TokenType.ACCESS,
        )
        user = await self.users.get_alive(payload.user_id)
        if user is None:
            raise UserNotFoundError("Account no longer exists")
        if not user.is_active:
            raise InactiveUserError
        return user

    # ----------------------------------------------------------------- refresh
    async def refresh(
        self,
        *,
        refresh_token: str,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> TokenPair:
        """Rotate a refresh token.

        Reuse detection: if the presented token is found but already consumed, every
        token of the same family is revoked before the error is raised.
        """
        payload = decode_token(
            refresh_token, settings=self.settings, expected_type=TokenType.REFRESH
        )

        stored = await self.tokens.get_by_jti(payload.jti)
        if stored is None or not hash_token(refresh_token) == stored.token_hash:
            raise InvalidCredentialsError("Refresh token is not recognised")

        if stored.was_used or stored.revoked_at is not None:
            revoked = await self.tokens.revoke_family(stored.family_id)
            await self.session.commit()
            logger.error(
                "refresh_token_reuse_detected",
                user_id=str(stored.user_id),
                family_id=str(stored.family_id),
                revoked=revoked,
            )
            raise RefreshTokenReuseError

        if stored.expires_at <= datetime.now(UTC):
            await self.session.commit()
            raise InvalidCredentialsError("Refresh token has expired")

        user = await self.users.get_alive(stored.user_id)
        if user is None or not user.is_active:
            raise InactiveUserError

        # Mark the old token consumed before issuing its successor.
        stored.used_at = datetime.now(UTC)
        await self.session.flush()

        tokens = await self._issue_token_pair(
            user=user,
            family_id=stored.family_id,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        await self.session.commit()
        logger.info("token_rotated", user_id=str(user.id), family_id=str(stored.family_id))
        return tokens

    # ------------------------------------------------------------------ logout
    async def logout(self, *, refresh_token: str) -> None:
        """Revoke the presented refresh token (idempotent)."""
        payload = decode_token(
            refresh_token, settings=self.settings, expected_type=TokenType.REFRESH
        )
        stored = await self.tokens.get_by_jti(payload.jti)
        if stored is None:
            return
        stored.revoked_at = datetime.now(UTC)
        await self.session.commit()
        logger.info("logout", user_id=str(stored.user_id))

    async def logout_all(self, *, user_id: uuid.UUID) -> int:
        """Revoke every active session of a user; returns how many were closed."""
        revoked = await self.tokens.revoke_all_for_user(user_id)
        await self.session.commit()
        logger.info("logout_all", user_id=str(user_id), revoked=revoked)
        return revoked

    # --------------------------------------------------------------- passwords
    async def change_password(self, *, user: User, payload: PasswordChange) -> None:
        """Change the password and close every other session."""
        payload.assert_changed()
        if not verify_password(payload.current_password, user.hashed_password):
            raise InvalidCredentialsError("Current password is incorrect")

        user.hashed_password = hash_password(
            payload.new_password,
            rounds=self.settings.bcrypt_rounds,
        )
        await self.users.insert(user, flush=False)
        revoked = await self.tokens.revoke_all_for_user(user.id)
        await self.session.commit()
        logger.info("password_changed", user_id=str(user.id), sessions_revoked=revoked)

    async def maybe_upgrade_password_hash(self, user: User, *, plain_password: str) -> bool:
        """Re-hash a stored password when the cost factor no longer matches the config.

        Called right after a successful login, when the plain password is still in
        memory and bcrypt rounds may have been raised since the hash was written.
        """
        if needs_rehash(user.hashed_password, rounds=self.settings.bcrypt_rounds):
            user.hashed_password = hash_password(
                plain_password,
                rounds=self.settings.bcrypt_rounds,
            )
            await self.session.flush()
            logger.info("password_hash_upgraded", user_id=str(user.id))
            return True
        return False

    # ------------------------------------------------------------------ helpers
    async def _issue_token_pair(
        self,
        *,
        user: User,
        family_id: uuid.UUID | None = None,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> TokenPair:
        """Mint an access + refresh pair and persist the refresh token."""
        access_ttl = timedelta(minutes=self.settings.access_token_expire_minutes)
        access = create_token(
            subject=user.id,
            token_type=TokenType.ACCESS,
            settings=self.settings,
            expires_delta=access_ttl,
            role=user.role.value,
        )

        family = family_id or uuid.uuid4()
        refresh = create_token(
            subject=user.id,
            token_type=TokenType.REFRESH,
            settings=self.settings,
            family_id=family,
            additional_claims={"username": user.username},
        )

        record = RefreshToken(
            user_id=user.id,
            jti=refresh.jti,
            family_id=family,
            token_hash=hash_token(refresh.token),
            expires_at=refresh.expires_at,
            user_agent=(user_agent or "")[:256] or None,
            ip_address=ip_address,
        )
        await self.tokens.insert(record)

        return TokenPair(
            access_token=access.token,
            refresh_token=refresh.token,
            expires_in=int(access_ttl.total_seconds()),
            user=user,
        )

    async def invalidate_user_cache(self, user_id: uuid.UUID) -> None:
        """Drop cached user representations after a profile change."""
        await invalidate(make_key("user", user_id), f"{CACHE_PREFIX}:stats:{user_id}")
