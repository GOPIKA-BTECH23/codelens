"""Typed application errors and the one place that turns every failure into the standard shape.

Every error response looks like: {"error": {"code": "...", "message": "..."}}
Validation errors add a "details" list of {loc, msg}.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("app.errors")


class AppError(Exception):
    """Base class for errors we raise on purpose. Subclass it instead of raising HTTPException."""

    status_code = 500
    code = "internal_error"
    default_message = "Internal server error"

    def __init__(
        self, message: str | None = None, *, headers: dict[str, str] | None = None
    ) -> None:
        self.message = message or self.default_message
        self.headers = headers or {}
        super().__init__(self.message)


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    default_message = "Resource not found"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"
    default_message = "You do not have permission to do this"


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"
    default_message = "Authentication required"


class TokenExpiredError(UnauthorizedError):
    """The access token was valid but has expired. The web client refreshes on any 401;
    the distinct code just makes logs and debugging clearer."""

    code = "token_expired"
    default_message = "Access token expired"


class InvalidTokenError(UnauthorizedError):
    code = "invalid_token"
    default_message = "Invalid credentials"


class RateLimitedError(AppError):
    status_code = 429
    code = "rate_limited"
    default_message = "Too many requests, slow down"

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(headers={"Retry-After": str(retry_after_seconds)})


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "service_unavailable"
    default_message = "Service unavailable"


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    *,
    details: object | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build a response in the standard error shape.

    Raise an AppError instead wherever possible. Return this directly only when the
    request must still COMMIT (raising rolls the session back), e.g. refresh-token reuse:
    the family revocation has to be saved even though the client gets a 401.
    """
    body: dict[str, object] = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    response = JSONResponse(status_code=status_code, content={"error": body}, headers=headers)
    # 500s are produced outside our middleware, so set the header here too.
    request_id = request.scope.get("state", {}).get("request_id")
    if request_id:
        response.headers["X-Request-ID"] = request_id
    return response


async def _handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return error_response(request, exc.status_code, exc.code, exc.message, headers=exc.headers)


async def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    # Framework-raised errors (unknown route, wrong method, ...).
    return error_response(request, exc.status_code, f"http_{exc.status_code}", str(exc.detail))


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Only field location and message go back; never echo the submitted input.
    details = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
    return error_response(
        request, 422, "validation_error", "Request validation failed", details=details
    )


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    # Full traceback goes to the logs (with the request ID); the client gets nothing internal.
    logger.error("unhandled exception", exc_info=exc, extra={"path": request.url.path})
    return error_response(request, 500, "internal_error", "Internal server error")


def register_error_handlers(app: FastAPI) -> None:
    """Install the global handlers on the app."""
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unexpected)
