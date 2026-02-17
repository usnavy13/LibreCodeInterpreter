"""Error models and exception classes for the Code Interpreter API."""

import time
from typing import Optional, List
from pydantic import BaseModel, Field
from enum import Enum


class ErrorType(str, Enum):
    """Error type enumeration."""

    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    VALIDATION = "validation"
    RESOURCE_NOT_FOUND = "resource_not_found"
    RESOURCE_CONFLICT = "resource_conflict"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    EXECUTION_FAILED = "execution_failed"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    INTERNAL_SERVER = "internal_server"
    SERVICE_UNAVAILABLE = "service_unavailable"
    EXTERNAL_SERVICE = "external_service"


class ErrorDetail(BaseModel):
    """Detailed error information."""

    field: Optional[str] = Field(None, description="Field name for validation errors")
    message: str = Field(..., description="Human-readable error message")
    code: Optional[str] = Field(None, description="Machine-readable error code")


class ErrorResponse(BaseModel):
    """Standardized error response model."""

    error: str = Field(..., description="Main error message")
    error_type: ErrorType = Field(..., description="Error category")
    details: Optional[List[ErrorDetail]] = Field(
        None, description="Additional error details"
    )
    request_id: Optional[str] = Field(
        None, description="Request identifier for tracking"
    )
    timestamp: float = Field(default_factory=time.time, description="Error timestamp")

    class Config:
        use_enum_values = True


# Custom Exception Classes


class CodeInterpreterException(Exception):
    """Base exception for Code Interpreter API."""

    def __init__(
        self,
        message: str,
        error_type: ErrorType = ErrorType.INTERNAL_SERVER,
        status_code: int = 500,
        details: Optional[List[ErrorDetail]] = None,
        request_id: Optional[str] = None,
    ):
        self.message = message
        self.error_type = error_type
        self.status_code = status_code
        self.details = details or []
        self.request_id = request_id
        super().__init__(message)

    def to_response(self) -> ErrorResponse:
        """Convert exception to error response model."""
        return ErrorResponse(
            error=self.message,
            error_type=self.error_type,
            details=self.details if self.details else None,
            request_id=self.request_id,
        )


class ValidationError(CodeInterpreterException):
    """Request validation errors."""

    def __init__(self, message: str = "Validation failed", **kwargs):
        super().__init__(
            message=message, error_type=ErrorType.VALIDATION, status_code=400, **kwargs
        )


class ServiceUnavailableError(CodeInterpreterException):
    """Service unavailable errors."""

    def __init__(self, service: str, message: str = None, **kwargs):
        error_message = message or f"{service} service is currently unavailable"
        super().__init__(
            message=error_message,
            error_type=ErrorType.SERVICE_UNAVAILABLE,
            status_code=503,
            **kwargs,
        )
