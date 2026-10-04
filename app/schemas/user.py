"""User schemas."""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import EmailStr, Field, field_validator

from app.core.constants import Role
from app.schemas.common import BaseSchema

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,64}$")
PASSWORD_MIN_LENGTH = 8
#: A password must mix at least this many character classes (lower/upper/digit).
PASSWORD_MIN_CHAR_CLASSES = 2


class UserCreate(BaseSchema):
    """Registration payload."""

    email: EmailStr = Field(max_length=320, examples=["dev@taskhub.dev"])
    username: str = Field(min_length=3, max_length=64, examples=["dev"])
    password: str = Field(
        min_length=PASSWORD_MIN_LENGTH, max_length=128, examples=["Str0ngPassw0rd!"]
    )
    full_name: str | None = Field(default=None, max_length=200)

    @field_validator("username")
    @classmethod
    def _validate_username(cls, value: str) -> str:
        cleaned = value.strip()
        if not USERNAME_RE.match(cleaned):
            raise ValueError(
                "username may only contain letters, digits, dot, dash and underscore "
                "(3-64 characters)"
            )
        return cleaned.lower()

    @field_validator("password")
    @classmethod
    def _validate_password_strength(cls, value: str) -> str:
        """Reject passwords that would fall to a dictionary attack in seconds."""
        checks = (
            any(char.islower() for char in value),
            any(char.isupper() for char in value),
            any(char.isdigit() for char in value),
        )
        if sum(checks) < PASSWORD_MIN_CHAR_CLASSES:
            raise ValueError(
                "password must mix lower case, upper case and digits (at least two of them)"
            )
        return value


class UserUpdate(BaseSchema):
    """Patch payload — every field optional, `None` means "leave untouched"."""

    full_name: str | None = Field(default=None, max_length=200)
    email: EmailStr | None = Field(default=None, max_length=320)
    username: str | None = Field(default=None, min_length=3, max_length=64)
    is_active: bool | None = None


class UserRead(BaseSchema):
    """Full user representation returned to the owner or to staff."""

    id: uuid.UUID
    email: EmailStr
    username: str
    full_name: str | None
    role: Role
    is_active: bool
    is_superuser: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class UserSummary(BaseSchema):
    """Compact user shape embedded in other resources."""

    id: uuid.UUID
    username: str
    full_name: str | None


class PasswordChange(BaseSchema):
    """Own-password change; revokes every refresh token of the account."""

    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _validate_password_strength(cls, value: str) -> str:
        return UserCreate._validate_password_strength(value)

    def assert_changed(self) -> None:
        if self.current_password == self.new_password:
            raise ValueError("new password must differ from the current one")
