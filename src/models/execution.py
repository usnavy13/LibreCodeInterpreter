"""Execution data models for the Code Interpreter API."""

# Standard library imports
from datetime import datetime
from enum import Enum
from typing import List, Optional

# Third-party imports
from pydantic import BaseModel, Field


class ExecutionStatus(str, Enum):
    """Execution status enumeration."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class OutputType(str, Enum):
    """Output type enumeration."""

    STDOUT = "stdout"
    STDERR = "stderr"
    IMAGE = "image"
    FILE = "file"
    ERROR = "error"


class ExecutionOutput(BaseModel):
    """Model for execution output."""

    type: OutputType
    content: str = Field(..., description="Output content or file path")
    mime_type: Optional[str] = Field(
        default=None, description="MIME type for file outputs"
    )
    size: Optional[int] = Field(
        default=None, description="Size in bytes for file outputs"
    )
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class CodeExecution(BaseModel):
    """Model for code execution request and response."""

    execution_id: str = Field(..., description="Unique execution identifier")
    session_id: str = Field(..., description="Associated session ID")
    code: str = Field(..., description="Code to execute")
    language: str = Field(default="py", description="Programming language")

    # Execution state
    status: ExecutionStatus = Field(default=ExecutionStatus.PENDING)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)

    # Results
    outputs: List[ExecutionOutput] = Field(default_factory=list)
    exit_code: Optional[int] = Field(default=None)
    error_message: Optional[str] = Field(default=None)

    # Resource usage
    execution_time_ms: Optional[int] = Field(default=None)
    memory_peak_mb: Optional[float] = Field(default=None)

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class ExecuteCodeRequest(BaseModel):
    """Request model for code execution."""

    code: str = Field(..., description="Code to execute", min_length=1)
    language: str = Field(default="py", description="Programming language")
    timeout: Optional[int] = Field(
        default=None, description="Execution timeout in seconds"
    )
    args: Optional[List[str]] = Field(
        default=None, description="Command line arguments to pass to the executed code"
    )


class ExecuteCodeResponse(BaseModel):
    """Response model for code execution."""

    execution_id: str
    status: ExecutionStatus
    outputs: List[ExecutionOutput] = Field(default_factory=list)
    exit_code: Optional[int] = None
    error_message: Optional[str] = None
    execution_time_ms: Optional[int] = None

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}
