"""
Pydantic models for the executor service API.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class FileReference(BaseModel):
    """Reference to a file to be made available during execution."""

    file_id: str
    filename: str
    content: Optional[bytes] = None  # Raw content bytes
    content_b64: Optional[str] = None  # Base64-encoded content (for HTTP transfer)
    path: Optional[str] = None  # Path in working directory

    def get_content(self) -> Optional[bytes]:
        """Get file content, decoding from base64 if needed.

        Returns:
            File content as bytes, or None if not available
        """
        if self.content:
            return self.content
        if self.content_b64:
            import base64
            return base64.b64decode(self.content_b64)
        return None


class ExecuteRequest(BaseModel):
    """Request to execute code."""

    code: str = Field(..., description="The code to execute")
    language: str = Field(..., description="Programming language (py, js, ts, go, java, c, cpp, php, rs, r, f90, d)")
    timeout: int = Field(default=30, ge=1, le=300, description="Execution timeout in seconds")
    session_id: Optional[str] = Field(default=None, description="Session ID for tracking")
    files: Optional[List[FileReference]] = Field(default=None, description="Files to mount")
    args: Optional[List[str]] = Field(default=None, description="Command line arguments")

    # Python-specific state handling
    initial_state: Optional[str] = Field(default=None, description="Base64-encoded Python state to restore")
    capture_state: bool = Field(default=True, description="Whether to capture Python state after execution")


class ExecuteResponse(BaseModel):
    """Response from code execution."""

    exit_code: int = Field(..., description="Exit code from execution")
    stdout: str = Field(default="", description="Standard output")
    stderr: str = Field(default="", description="Standard error")
    execution_time_ms: float = Field(..., description="Execution time in milliseconds")

    # Python state
    state: Optional[str] = Field(default=None, description="Base64-encoded Python state")
    state_errors: List[str] = Field(default_factory=list, description="Errors during state capture")

    # Generated files
    generated_files: List[Dict[str, Any]] = Field(default_factory=list, description="Files created during execution")

    # Error info
    error: Optional[str] = Field(default=None, description="Error message if execution failed")
    timed_out: bool = Field(default=False, description="Whether execution timed out")


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = Field(default="healthy")
    version: str = Field(default="1.0.0")
    languages: List[str] = Field(default_factory=list)
    concurrent_executions: int = Field(default=0)
    max_concurrent: int = Field(default=4)
