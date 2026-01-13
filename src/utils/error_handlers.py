"""Global error handlers for the Code Interpreter API."""

# Standard library imports
import traceback
from typing import Union

# Third-party imports
import structlog
from fastapi import Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError

# Local application imports
from ..models.errors import (
    CodeInterpreterException,
    ErrorResponse,
    ErrorType,
    ErrorDetail,
)

logger = structlog.get_logger(__name__)


def generate_request_id() -> str:
    """Generate a unique request ID for error tracking."""
    from .id_generator import generate_request_id as gen_id

    return gen_id()


async def code_interpreter_exception_handler(
    request: Request, exc: CodeInterpreterException
) -> JSONResponse:
    """Handle custom CodeInterpreterException instances."""

    # Generate request ID if not present
    if not exc.request_id:
        exc.request_id = generate_request_id()

    # Log the error with appropriate level
    log_data = {
        "error_type": exc.error_type.value,
        "status_code": exc.status_code,
        "message": exc.message,
        "request_id": exc.request_id,
        "path": request.url.path,
        "method": request.method,
        "client_ip": (
            getattr(request.client, "host", "unknown") if request.client else "unknown"
        ),
    }

    # Add details if present
    if exc.details:
        log_data["details"] = [
            {"field": d.field, "message": d.message, "code": d.code}
            for d in exc.details
        ]

    # Log with appropriate level based on error type
    if exc.status_code >= 500:
        logger.error("Server error occurred", **log_data)
    elif exc.status_code >= 400:
        logger.warning("Client error occurred", **log_data)
    else:
        logger.info("Error handled", **log_data)

    # Return standardized error response
    error_response = exc.to_response()
    return JSONResponse(
        status_code=exc.status_code, content=error_response.model_dump()
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Handle FastAPI HTTPException instances."""

    request_id = generate_request_id()

    # Map HTTP status codes to error types
    error_type_mapping = {
        400: ErrorType.VALIDATION,
        401: ErrorType.AUTHENTICATION,
        403: ErrorType.AUTHORIZATION,
        404: ErrorType.RESOURCE_NOT_FOUND,
        409: ErrorType.RESOURCE_CONFLICT,
        413: ErrorType.RESOURCE_EXHAUSTED,
        415: ErrorType.VALIDATION,
        422: ErrorType.VALIDATION,
        429: ErrorType.RATE_LIMITED,
        500: ErrorType.INTERNAL_SERVER,
        502: ErrorType.EXTERNAL_SERVICE,
        503: ErrorType.SERVICE_UNAVAILABLE,
        504: ErrorType.TIMEOUT,
    }

    error_type = error_type_mapping.get(exc.status_code, ErrorType.INTERNAL_SERVER)

    # Log the error
    logger.warning(
        "HTTP exception occurred",
        status_code=exc.status_code,
        detail=exc.detail,
        request_id=request_id,
        path=request.url.path,
        method=request.method,
        client_ip=(
            getattr(request.client, "host", "unknown") if request.client else "unknown"
        ),
    )

    # Create standardized error response
    error_response = ErrorResponse(
        error=str(exc.detail), error_type=error_type, request_id=request_id
    )

    return JSONResponse(
        status_code=exc.status_code, content=error_response.model_dump()
    )


async def validation_exception_handler(
    request: Request, exc: Union[RequestValidationError, PydanticValidationError]
) -> JSONResponse:
    """Handle request validation errors."""

    request_id = generate_request_id()

    # Extract validation error details
    details = []
    for error in exc.errors():
        field_path = " -> ".join(str(loc) for loc in error["loc"])
        details.append(
            ErrorDetail(field=field_path, message=error["msg"], code=error["type"])
        )

    # Log validation error
    logger.warning(
        "Validation error occurred",
        request_id=request_id,
        path=request.url.path,
        method=request.method,
        validation_errors=[
            {"field": d.field, "message": d.message, "code": d.code} for d in details
        ],
        client_ip=(
            getattr(request.client, "host", "unknown") if request.client else "unknown"
        ),
    )

    # Create standardized error response
    error_response = ErrorResponse(
        error="Request validation failed",
        error_type=ErrorType.VALIDATION,
        details=details,
        request_id=request_id,
    )

    return JSONResponse(status_code=422, content=error_response.model_dump())


async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected exceptions."""

    request_id = generate_request_id()

    # Log the full exception with traceback
    logger.error(
        "Unexpected exception occurred",
        request_id=request_id,
        path=request.url.path,
        method=request.method,
        exception_type=type(exc).__name__,
        exception_message=str(exc),
        traceback=traceback.format_exc(),
        client_ip=(
            getattr(request.client, "host", "unknown") if request.client else "unknown"
        ),
    )

    # Create generic error response (don't expose internal details)
    error_response = ErrorResponse(
        error="An unexpected error occurred",
        error_type=ErrorType.INTERNAL_SERVER,
        request_id=request_id,
    )

    return JSONResponse(status_code=500, content=error_response.model_dump())
