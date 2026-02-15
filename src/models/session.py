"""Session data models for the Code Interpreter API."""

# Standard library imports
from datetime import datetime
from enum import Enum
from typing import Dict, Optional, Any

# Third-party imports
from pydantic import BaseModel, Field


class SessionStatus(str, Enum):
    """Session status enumeration."""

    ACTIVE = "active"
    IDLE = "idle"
    TERMINATED = "terminated"
    ERROR = "error"


class FileInfo(BaseModel):
    """Information about a file in the session."""

    filename: str
    size: int
    mime_type: str
    created_at: datetime
    path: str


class Session(BaseModel):
    """Session model representing a code execution environment."""

    session_id: str = Field(..., description="Unique session identifier")
    status: SessionStatus = Field(
        default=SessionStatus.ACTIVE, description="Current session status"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow, description="Session creation timestamp"
    )
    last_activity: datetime = Field(
        default_factory=datetime.utcnow, description="Last activity timestamp"
    )
    expires_at: datetime = Field(..., description="Session expiration timestamp")

    # Sandbox information
    container_id: Optional[str] = Field(default=None, description="Sandbox ID")
    container_status: Optional[str] = Field(
        default=None, description="Sandbox status"
    )

    # File management
    files: Dict[str, FileInfo] = Field(
        default_factory=dict, description="Files in the session"
    )
    working_directory: str = Field(
        default="/mnt/data", description="Working directory path"
    )

    # Resource usage
    memory_usage_mb: Optional[float] = Field(
        default=None, description="Current memory usage in MB"
    )
    cpu_usage_percent: Optional[float] = Field(
        default=None, description="Current CPU usage percentage"
    )

    # Metadata
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Additional session metadata"
    )

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class SessionCreate(BaseModel):
    """Request model for creating a new session."""

    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Optional session metadata"
    )


class SessionResponse(BaseModel):
    """Response model for session operations."""

    session_id: str
    status: SessionStatus
    created_at: datetime
    expires_at: datetime
    message: Optional[str] = None

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}
