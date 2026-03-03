"""Service interfaces for the Code Interpreter API."""

# Standard library imports
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any, Tuple

# Local application imports
from ..models import (
    Session,
    SessionCreate,
    CodeExecution,
    ExecuteCodeRequest,
    FileInfo,
    FileUploadRequest,
)


class SessionServiceInterface(ABC):
    """Interface for session management service."""

    @abstractmethod
    async def create_session(self, request: SessionCreate) -> Session:
        """Create a new code execution session."""
        pass

    @abstractmethod
    async def get_session(self, session_id: str) -> Optional[Session]:
        """Retrieve a session by ID."""
        pass

    @abstractmethod
    async def update_session(self, session_id: str, **updates) -> Optional[Session]:
        """Update session properties."""
        pass

    @abstractmethod
    async def delete_session(self, session_id: str) -> bool:
        """Delete a session and cleanup resources."""
        pass

    @abstractmethod
    async def list_sessions(self, limit: int = 100, offset: int = 0) -> List[Session]:
        """List all active sessions."""
        pass

    @abstractmethod
    async def cleanup_expired_sessions(self) -> int:
        """Clean up expired sessions and return count of cleaned sessions."""
        pass


class ExecutionServiceInterface(ABC):
    """Interface for code execution service."""

    @abstractmethod
    async def execute_code(
        self,
        session_id: str,
        request: ExecuteCodeRequest,
        files: Optional[List[Dict[str, Any]]] = None,
    ) -> CodeExecution:
        """Execute code in a session."""
        pass

    @abstractmethod
    async def get_execution(self, execution_id: str) -> Optional[CodeExecution]:
        """Retrieve an execution by ID."""
        pass

    @abstractmethod
    async def cancel_execution(self, execution_id: str) -> bool:
        """Cancel a running execution."""
        pass

    @abstractmethod
    async def list_executions(
        self, session_id: str, limit: int = 100
    ) -> List[CodeExecution]:
        """List executions for a session."""
        pass


class FileServiceInterface(ABC):
    """Interface for file management service."""

    @abstractmethod
    async def upload_file(
        self, session_id: str, request: FileUploadRequest
    ) -> Tuple[str, str]:
        """Generate upload URL for a file. Returns (file_id, upload_url)."""
        pass

    @abstractmethod
    async def confirm_upload(self, session_id: str, file_id: str) -> FileInfo:
        """Confirm file upload completion and return file info."""
        pass

    @abstractmethod
    async def get_file_info(self, session_id: str, file_id: str) -> Optional[FileInfo]:
        """Get file information."""
        pass

    @abstractmethod
    async def list_files(self, session_id: str) -> List[FileInfo]:
        """List all files in a session."""
        pass

    @abstractmethod
    async def download_file(self, session_id: str, file_id: str) -> Optional[str]:
        """Generate download URL for a file."""
        pass

    @abstractmethod
    async def get_file_content(self, session_id: str, file_id: str) -> Optional[bytes]:
        """Get actual file content."""
        pass

    @abstractmethod
    async def delete_file(self, session_id: str, file_id: str) -> bool:
        """Delete a file from storage."""
        pass

    @abstractmethod
    async def cleanup_session_files(self, session_id: str) -> int:
        """Clean up all files for a session. Returns count of deleted files."""
        pass
