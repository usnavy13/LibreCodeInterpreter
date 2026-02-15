"""Sandbox lifecycle management using nsjail."""

import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import structlog

from ...config import settings
from ...config.languages import get_user_id_for_language
from .nsjail import NsjailConfig, SandboxInfo
from .executor import SandboxExecutor

logger = structlog.get_logger(__name__)


class SandboxManager:
    """Manages nsjail sandbox lifecycle operations.

    Creates sandbox directories on the host filesystem for isolated
    code execution via nsjail.
    """

    def __init__(self):
        """Initialize the sandbox manager."""
        self._nsjail_config = NsjailConfig()
        self._executor = SandboxExecutor(self._nsjail_config)
        self._base_dir = Path(settings.sandbox_base_dir)
        self._initialization_error: Optional[str] = None

        # Ensure base directory exists
        try:
            self._base_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self._initialization_error = (
                f"Failed to create sandbox base directory {self._base_dir}: {e}"
            )
            logger.error(
                "Sandbox base directory creation failed",
                base_dir=str(self._base_dir),
                error=str(e),
            )

    @property
    def executor(self) -> SandboxExecutor:
        """Get the sandbox executor."""
        return self._executor

    def is_available(self) -> bool:
        """Check if nsjail is available."""
        return shutil.which(settings.nsjail_binary) is not None

    def get_initialization_error(self) -> Optional[str]:
        """Get initialization error if any."""
        if self._initialization_error:
            return self._initialization_error
        if not self.is_available():
            return (
                f"nsjail binary not found: {settings.nsjail_binary}. "
                "Ensure nsjail is installed and in PATH."
            )
        return None

    def create_sandbox(
        self,
        session_id: str,
        language: str,
        repl_mode: bool = False,
    ) -> SandboxInfo:
        """Create a new sandbox directory.

        Args:
            session_id: Session identifier
            language: Programming language code
            repl_mode: Whether to start in REPL mode

        Returns:
            SandboxInfo with paths to the sandbox directories
        """
        sandbox_id = uuid.uuid4().hex
        sandbox_dir = self._base_dir / sandbox_id
        data_dir = sandbox_dir / "data"

        try:
            data_dir.mkdir(parents=True, exist_ok=True)

            # Make data dir writable by the sandbox user.
            # Each sandbox has its own isolated directory so world-writable is safe.
            os.chmod(str(data_dir), 0o777)
        except OSError as e:
            logger.error(
                "Failed to create sandbox directory",
                sandbox_id=sandbox_id,
                error=str(e),
            )
            raise RuntimeError(f"Failed to create sandbox: {e}")

        labels = {
            "com.code-interpreter.managed": "true",
            "com.code-interpreter.type": "execution",
            "com.code-interpreter.session-id": session_id,
            "com.code-interpreter.language": language or "unknown",
            "com.code-interpreter.created-at": datetime.utcnow().isoformat(),
            "com.code-interpreter.repl-mode": "true" if repl_mode else "false",
        }

        info = SandboxInfo(
            sandbox_id=sandbox_id,
            sandbox_dir=sandbox_dir,
            data_dir=data_dir,
            language=language,
            session_id=session_id,
            created_at=datetime.utcnow(),
            repl_mode=repl_mode,
            labels=labels,
        )

        logger.info(
            "Created sandbox",
            sandbox_id=sandbox_id[:12],
            session_id=session_id[:12] if session_id else "none",
            language=language,
            repl_mode=repl_mode,
        )

        return info

    def destroy_sandbox(self, sandbox_info: SandboxInfo) -> bool:
        """Destroy a sandbox by removing its directory tree.

        Args:
            sandbox_info: Sandbox to destroy

        Returns:
            True if successful, False otherwise
        """
        try:
            if sandbox_info.sandbox_dir.exists():
                shutil.rmtree(str(sandbox_info.sandbox_dir))
            logger.debug(
                "Destroyed sandbox",
                sandbox_id=sandbox_info.sandbox_id[:12],
            )
            return True
        except Exception as e:
            logger.warning(
                "Failed to destroy sandbox",
                sandbox_id=sandbox_info.sandbox_id[:12],
                error=str(e),
            )
            return False

    def copy_content_to_sandbox(
        self,
        sandbox_info: SandboxInfo,
        content: bytes,
        dest_path: str,
        language: str = "py",
    ) -> bool:
        """Write file content into the sandbox data directory.

        Args:
            sandbox_info: Target sandbox
            content: File content as bytes
            dest_path: Destination path (e.g., /mnt/data/file.py or file.py)
            language: Programming language (used to set correct ownership)

        Returns:
            True if successful, False otherwise
        """
        try:
            # Extract filename from dest_path (may be absolute like /mnt/data/file.py)
            filename = Path(dest_path).name
            file_path = sandbox_info.data_dir / filename

            file_path.write_bytes(content)

            # Set ownership to language-specific user
            user_id = get_user_id_for_language(language.lower().strip())
            os.chown(str(file_path), user_id, user_id)
            os.chmod(str(file_path), 0o644)

            return True
        except Exception as e:
            logger.error(
                "Failed to copy content to sandbox",
                sandbox_id=sandbox_info.sandbox_id[:12],
                dest_path=dest_path,
                error=str(e),
            )
            return False

    def get_file_content_from_sandbox(
        self, sandbox_info: SandboxInfo, source_path: str
    ) -> Optional[bytes]:
        """Read file content from the sandbox data directory.

        Args:
            sandbox_info: Source sandbox
            source_path: Path to file (may be absolute like /mnt/data/file.py)

        Returns:
            File content as bytes, or None if failed
        """
        try:
            # Extract filename from source_path (may be absolute)
            filename = Path(source_path).name
            file_path = sandbox_info.data_dir / filename

            if file_path.exists():
                return file_path.read_bytes()

            # Try the full path relative to data_dir
            if source_path.startswith("/mnt/data/"):
                relative = source_path[len("/mnt/data/"):]
                alt_path = sandbox_info.data_dir / relative
                if alt_path.exists():
                    return alt_path.read_bytes()

            logger.warning(
                "File not found in sandbox",
                sandbox_id=sandbox_info.sandbox_id[:12],
                source_path=source_path,
            )
            return None
        except Exception as e:
            logger.error(
                "Failed to get file content from sandbox",
                sandbox_id=sandbox_info.sandbox_id[:12],
                source_path=source_path,
                error=str(e),
            )
            return None

    async def execute_command(
        self,
        sandbox_info: SandboxInfo,
        command: str,
        timeout: int = None,
        language: Optional[str] = None,
        stdin_payload: Optional[str] = None,
    ) -> Tuple[int, str, str]:
        """Execute a command inside the sandbox via nsjail.

        Args:
            sandbox_info: Sandbox to execute in
            command: Command string to execute
            timeout: Execution timeout in seconds
            language: Programming language code
            stdin_payload: Optional stdin data

        Returns:
            Tuple of (exit_code, stdout, stderr)
        """
        return await self._executor.execute_command(
            sandbox_info, command, timeout, language, stdin_payload
        )

    def get_user_id_for_language(self, language: str) -> int:
        """Get the user ID for a language sandbox."""
        return get_user_id_for_language(language.lower().strip())

    def close(self):
        """Clean up resources. No-op for sandbox manager."""
        pass
