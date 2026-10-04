"""Exception handlers.

One translation point from domain errors to HTTP. That is what keeps the error envelope
identical for a 404, a 409 raised by the service layer and a 422 produced by pydantic.
"""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.constants import ErrorCode
from app.core.errors import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)

_STATUS_TO_CODE: dict[int, ErrorCode] = {
    status.HTTP_400_BAD_REQUEST: ErrorCode.VALIDATION_ERROR,
    status.HTTP_401_UNAUTHORIZED: ErrorCode.AUTHENTICATION_ERROR,
    status.HTTP_403_FORBIDDEN: ErrorCode.PERMISSION_DENIED,
    status.HTTP_404_NOT_FOUND: ErrorCode.NOT_FOUND,
    status.HTTP_405_METHOD_NOT_ALLOWED: ErrorCode.VALIDATION_ERROR,
    status.HTTP_409_CONFLICT: ErrorCode.CONFLICT,
    422: ErrorCode.VALIDATION_ERROR,
    status.HTTP_429_TOO_MANY_REQUESTS: ErrorCode.RATE_LIMITED,
    status.HTTP_503_SERVICE_UNAVAILABLE: ErrorCode.SERVICE_UNAVAILABLE,
}


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _envelope(
    request: Request,
    *,
    status_code: int,
    code: ErrorCode,
    message: str,
    details: Mapping[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build the single error response shape used by every failure."""
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code.value,
                "message": message,
                "details": details or {},
                "request_id": _request_id(request),
            }
        },
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach every handler to the application.

    One function per exception type, all defined here on purpose: splitting them across
    modules would hide the fact that this is the single translation point.
    """

    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
            logger.error("app_error", code=exc.code.value, message=exc.message)
        else:
            logger.info(
                "app_error",
                code=exc.code.value,
                message=exc.message,
                status=exc.status_code,
                path=request.url.path,
            )
        headers = {}
        if exc.code is ErrorCode.AUTHENTICATION_ERROR:
            headers["WWW-Authenticate"] = "Bearer"
        return _envelope(
            request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Turn pydantic's error list into `field -> message`, dropping the noisy `loc`."""
        fields: dict[str, str] = {}
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"][1:]) or "body"
            fields.setdefault(location, error["msg"])
        return _envelope(
            request,
            # 422 as a literal: Starlette renamed the constant across versions and the
            # number is part of the HTTP contract, not of any library.
            status_code=422,
            code=ErrorCode.VALIDATION_ERROR,
            message="Request payload failed validation",
            details=fields,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_TO_CODE.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
        detail: str = exc.detail if isinstance(exc.detail, str) else "Request failed"
        headers = dict(exc.headers or {})
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            headers.setdefault("WWW-Authenticate", "Bearer")
        return _envelope(
            request,
            status_code=exc.status_code,
            code=code,
            message=detail,
            headers=headers or None,
        )

    @app.exception_handler(IntegrityError)
    async def _handle_integrity(request: Request, exc: IntegrityError) -> JSONResponse:
        """A database constraint fired: report a conflict, never leak the SQL."""
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        logger.warning("integrity_error", constraint=constraint, path=request.url.path)
        return _envelope(
            request,
            status_code=status.HTTP_409_CONFLICT,
            code=ErrorCode.CONFLICT,
            message="The change conflicts with existing data",
            details={"constraint": constraint} if constraint else {},
        )

    @app.exception_handler(SQLAlchemyError)
    async def _handle_database(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.exception("database_error", path=request.url.path)
        return _envelope(
            request,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="The database is unavailable, please retry",
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        """Last resort: log with full context, return an opaque message."""
        logger.exception(
            "unhandled_exception",
            path=request.url.path,
            method=request.method,
            request_id=_request_id(request),
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=jsonable_encoder(
                {
                    "error": {
                        "code": ErrorCode.INTERNAL_ERROR.value,
                        "message": "Internal server error",
                        "details": {},
                        "request_id": _request_id(request),
                    }
                }
            ),
        )
