"""File management data models for the Code Interpreter API."""

# Standard library imports
from datetime import datetime
from typing import List, Optional

# Third-party imports
from pydantic import BaseModel, Field


class FileUploadRequest(BaseModel):
    """Request model for file upload."""

    filename: str = Field(..., description="Name of the file")
    content_type: Optional[str] = Field(
        default=None, description="MIME type of the file"
    )


class FileUploadResponse(BaseModel):
    """Response model for file upload."""

    file_id: str = Field(..., description="Unique file identifier")
    filename: str
    size: int
    content_type: str
    upload_url: str = Field(..., description="Pre-signed URL for file upload")
    expires_at: datetime = Field(..., description="URL expiration time")

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class FileInfo(BaseModel):
    """File information model."""

    file_id: str
    filename: str
    size: int
    content_type: str
    created_at: datetime
    path: str = Field(..., description="File path in the session")
    # State restoration fields (for Python state-file linking)
    execution_id: Optional[str] = Field(
        default=None, description="ID of the execution that created/last used this file"
    )
    state_hash: Optional[str] = Field(
        default=None,
        description="SHA256 hash of the Python state when this file was last used",
    )
    last_used_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp of when this file was last used in an execution",
    )

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class FileListResponse(BaseModel):
    """Response model for listing files."""

    files: List[FileInfo]
    total_count: int
    total_size: int = Field(..., description="Total size of all files in bytes")


class FileDownloadResponse(BaseModel):
    """Response model for file download."""

    file_id: str
    filename: str
    download_url: str = Field(..., description="Pre-signed URL for file download")
    expires_at: datetime = Field(..., description="URL expiration time")

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class FileDeleteResponse(BaseModel):
    """Response model for file deletion."""

    file_id: str
    filename: str
    deleted: bool
    message: Optional[str] = None
