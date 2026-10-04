"""Problem-details responses and request-scoped security headers.

Guarantees: API errors contain a request id and never echo validation input or
internal exceptions (GUARANTEE(test_api_problem_shapes)).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from account_intelligence.api.errors import APIProblem, NotConfigured

logger = logging.getLogger(__name__)

_HTTP_PROBLEMS: dict[int, tuple[str, str, str]] = {
    401: ("invalid_credential", "Unauthorized", "A valid credential is required."),
    402: ("payment_required", "Payment required", "The request cannot be funded."),
    403: ("forbidden", "Forbidden", "This action is not permitted."),
    404: ("not_found", "Not found", "The requested resource was not found."),
    409: ("conflict", "Conflict", "The request conflicts with the current resource state."),
    422: ("invalid_request", "Invalid request", "The request did not satisfy the API contract."),
    429: ("rate_limited", "Too many requests", "The request rate limit was exceeded."),
}


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    if isinstance(value, str):
        return value
    value = str(uuid4())
    request.state.request_id = value
    return value


def _response(
    request: Request,
    *,
    status: int,
    problem_type: str,
    title: str,
    detail: str,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    request_id = _request_id(request)
    response_headers = dict(headers or {})
    response_headers["X-Request-ID"] = request_id
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        headers=response_headers,
        content={
            "type": problem_type,
            "title": title,
            "status": status,
            "detail": detail,
            "request_id": request_id,
        },
    )


def install_problem_handlers(app: FastAPI) -> None:
    """Install typed, non-leaking HTTP and validation exception handlers."""

    @app.exception_handler(APIProblem)
    async def api_problem_handler(request: Request, error: APIProblem) -> JSONResponse:
        return _response(
            request,
            status=error.status,
            problem_type=error.type,
            title=error.title,
            detail=error.detail,
            headers=error.headers,
        )

    @app.exception_handler(NotConfigured)
    async def not_configured_handler(request: Request, error: NotConfigured) -> JSONResponse:
        del error
        return _response(
            request,
            status=503,
            problem_type="dependency_not_configured",
            title="Service temporarily unavailable",
            detail="A required service is not configured.",
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_problem_handler(request: Request, error: StarletteHTTPException) -> JSONResponse:
        problem_type, title, detail = _HTTP_PROBLEMS.get(
            error.status_code,
            ("http_error", "Request failed", "The request could not be completed."),
        )
        return _response(
            request,
            status=error.status_code,
            problem_type=problem_type,
            title=title,
            detail=detail,
            headers=error.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_problem_handler(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        del error
        return _response(
            request,
            status=422,
            problem_type="invalid_request",
            title="Invalid request",
            detail="The request did not satisfy the API contract.",
        )

    @app.exception_handler(Exception)
    async def unexpected_problem_handler(request: Request, error: Exception) -> JSONResponse:
        logger.error(
            "Unhandled API exception",
            extra={"request_id": _request_id(request), "error_type": type(error).__name__},
        )
        return _response(
            request,
            status=500,
            problem_type="internal_error",
            title="Internal server error",
            detail="The request could not be completed.",
        )

    @app.middleware("http")
    async def request_id_middleware(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
