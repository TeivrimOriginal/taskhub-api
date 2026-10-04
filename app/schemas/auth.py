"""Authentication schemas."""

from __future__ import annotations

from pydantic import EmailStr, Field

from app.schemas.common import BaseSchema
from app.schemas.user import UserRead


class LoginRequest(BaseSchema):
    """JSON login (the OAuth2 form endpoint accepts the same fields)."""

    username: str = Field(min_length=3, max_length=320, description="E-mail or username")
    password: str = Field(min_length=1, max_length=128)


class RefreshTokenRequest(BaseSchema):
    """Body of `POST /auth/refresh`."""

    refresh_token: str = Field(min_length=20, max_length=4096)


class TokenPair(BaseSchema):
    """What a successful login/refresh returns."""

    access_token: str
    refresh_token: str
    token_type: str = Field(default="bearer", examples=["bearer"])
    expires_in: int = Field(description="Access token lifetime in seconds")
    user: UserRead | None = None


class AuthenticatedUser(BaseSchema):
    """Internal helper used by the auth service when only the id is needed."""

    id: str
    email: EmailStr
