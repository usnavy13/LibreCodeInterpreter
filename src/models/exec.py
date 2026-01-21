"""Models for the /exec endpoint compatible with LibreChat API."""

# Standard library imports
from datetime import datetime
from typing import List, Optional, Any

# Third-party imports
from pydantic import BaseModel, Field


class FileRef(BaseModel):
    """File reference model for execution response."""

    id: str
    name: str
    path: Optional[str] = None  # Make path optional


class RequestFile(BaseModel):
    """Request file model."""

    id: str
    session_id: str
    name: str
    restore_state: bool = Field(
        default=False,
        description="If true, restore Python state from when this file was last used"
    )


class ExecRequest(BaseModel):
    """Request model for /exec endpoint."""

    code: str = Field(..., description="The source code to be executed")
    lang: str = Field(..., description="The programming language of the code")
    # Accept any JSON type for args to avoid 422s when clients send objects/arrays
    args: Optional[Any] = Field(
        default=None, description="Optional command line arguments (any JSON type)"
    )
    user_id: Optional[str] = Field(default=None, description="Optional user identifier")
    entity_id: Optional[str] = Field(
        default=None,
        description="Optional assistant/agent identifier for file sharing",
        max_length=40,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    session_id: Optional[str] = Field(
        default=None,
        description="Optional session ID to continue an existing session (for state persistence)",
    )
    files: List[RequestFile] = Field(
        default_factory=list,
        description="Array of file references to be used during execution",
    )


class ExecResponse(BaseModel):
    """Response model for /exec endpoint - LibreChat compatible format."""

    session_id: str
    files: List[FileRef] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    # State persistence fields (Python only)
    has_state: bool = Field(
        default=False,
        description="Whether Python state was captured (Python executions only)",
    )
    state_size: Optional[int] = Field(
        default=None, description="Compressed state size in bytes"
    )
    state_hash: Optional[str] = Field(
        default=None, description="SHA256 hash for ETag/change detection"
    )

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}
