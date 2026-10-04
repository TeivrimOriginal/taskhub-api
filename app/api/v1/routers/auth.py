"""Authentication endpoints.

`/login` is exposed twice on purpose:

* `POST /auth/login` with **OAuth2 form fields**, because Swagger's *Authorize* button and
  every OAuth2 client expect `application/x-www-form-urlencoded`;
* the same handler accepts `LoginRequest` JSON through `/auth/login-json`, which is what a
  pure SPA usually finds easier.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.security import OAuth2PasswordRequestForm

from app.api.deps import AuthServiceDep, CurrentUser
from app.core.errors import AuthenticationError
from app.core.logging import get_logger
from app.models.user import User
from app.schemas.auth import LoginRequest, RefreshTokenRequest, TokenPair
from app.schemas.common import ErrorResponse, Message
from app.schemas.user import PasswordChange, UserCreate, UserRead, UserUpdate
from app.services.user import UserService

logger = get_logger(__name__)
router = APIRouter()

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse, "description": "Invalid credentials or token"},
    409: {"model": ErrorResponse, "description": "E-mail or username already taken"},
    422: {"model": ErrorResponse, "description": "Payload failed validation"},
}


@router.post(
    "/register",
    response_model=TokenPair,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new account",
    description="Creates the account and immediately returns a token pair.",
    responses=ERROR_RESPONSES,
    tags=["auth"],
)
async def register(payload: UserCreate, auth: AuthServiceDep) -> TokenPair:
    """Create an account and log it in, so a client needs only one round trip."""
    user = await auth.register(payload)
    tokens = await auth.login(identifier=user.email, password=payload.password)
    logger.info("registration_completed", user_id=str(user.id))
    return tokens


@router.post(
    "/login",
    response_model=TokenPair,
    summary="Log in with a password (OAuth2 form)",
    description="Accepts `username` (e-mail or handle) and `password` as form fields.",
    responses=ERROR_RESPONSES,
    tags=["auth"],
)
async def login_form(
    request: Request,
    auth: AuthServiceDep,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> TokenPair:
    return await _perform_login(request, auth, identifier=form.username, password=form.password)


@router.post(
    "/login-json",
    response_model=TokenPair,
    summary="Log in with a JSON body",
    description="Same behaviour as `/login`, for clients that prefer `application/json`.",
    responses=ERROR_RESPONSES,
    tags=["auth"],
)
async def login_json(
    request: Request,
    payload: LoginRequest,
    auth: AuthServiceDep,
) -> TokenPair:
    return await _perform_login(
        request, auth, identifier=payload.username, password=payload.password
    )


@router.post(
    "/refresh",
    response_model=TokenPair,
    summary="Rotate the refresh token",
    description=(
        "Returns a new pair and invalidates the presented refresh token. "
        "Re-using a consumed token revokes the whole session family."
    ),
    responses=ERROR_RESPONSES,
    tags=["auth"],
)
async def refresh(
    request: Request,
    payload: RefreshTokenRequest,
    auth: AuthServiceDep,
) -> TokenPair:
    return await auth.refresh(
        refresh_token=payload.refresh_token,
        user_agent=_user_agent(request),
        ip_address=_client_ip(request),
    )


@router.post(
    "/logout",
    response_model=Message,
    summary="Revoke the current refresh token",
    tags=["auth"],
)
async def logout(
    payload: RefreshTokenRequest,
    auth: AuthServiceDep,
    _current_user: CurrentUser,
) -> Message:
    await auth.logout(refresh_token=payload.refresh_token)
    return Message(message="Logged out")


@router.post(
    "/logout-all",
    response_model=Message,
    summary="Revoke every session of the current user",
    tags=["auth"],
)
async def logout_all(auth: AuthServiceDep, current_user: CurrentUser) -> Message:
    revoked = await auth.logout_all(user_id=current_user.id)
    return Message(message=f"Revoked {revoked} session(s)", detail={"revoked": revoked})


@router.get(
    "/me",
    response_model=UserRead,
    summary="Current user profile",
    tags=["auth"],
)
async def read_me(current_user: CurrentUser) -> UserRead:
    return UserRead.model_validate(current_user, from_attributes=True)


@router.patch(
    "/me",
    response_model=UserRead,
    summary="Update the current user profile",
    responses=ERROR_RESPONSES,
    tags=["auth"],
)
async def update_me(
    payload: UserUpdate,
    auth: AuthServiceDep,
    current_user: CurrentUser,
) -> UserRead:
    user: User = await UserService(auth.session).update(
        current_user.id, payload, actor=current_user
    )
    return UserRead.model_validate(user, from_attributes=True)


@router.post(
    "/change-password",
    response_model=Message,
    summary="Change the current user's password",
    description="Every session of the account is revoked afterwards.",
    responses=ERROR_RESPONSES,
    tags=["auth"],
)
async def change_password(
    payload: PasswordChange,
    auth: AuthServiceDep,
    current_user: CurrentUser,
) -> Message:
    await auth.change_password(user=current_user, payload=payload)
    return Message(message="Password changed, please log in again")


# ------------------------------------------------------------------- helpers
async def _perform_login(
    request: Request,
    auth: AuthServiceDep,
    *,
    identifier: str,
    password: str,
) -> TokenPair:
    if not identifier or not password:
        raise AuthenticationError("Both username and password are required")
    return await auth.login(
        identifier=identifier,
        password=password,
        user_agent=_user_agent(request),
        ip_address=_client_ip(request),
    )


def _user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
