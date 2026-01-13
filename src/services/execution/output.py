"""Output processing and validation for code execution."""

import os
import re
import secrets
from pathlib import Path
from typing import Any, Dict

import structlog

from ...config import settings
from ...models import ExecutionStatus

logger = structlog.get_logger(__name__)


class OutputProcessor:
    """Handles output sanitization, validation, and formatting."""

    # MIME type mapping
    MIME_TYPES = {
        ".txt": "text/plain",
        ".csv": "text/csv",
        ".json": "application/json",
        ".xml": "application/xml",
        ".html": "text/html",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".pdf": "application/pdf",
        ".zip": "application/zip",
    }

    @classmethod
    def sanitize_output(cls, output: str, max_size: int = 64 * 1024) -> str:
        """Sanitize execution output for security and display.

        Args:
            output: Raw output string
            max_size: Maximum output size in bytes (default 64KB)

        Returns:
            Sanitized output string
        """
        try:
            if len(output) > max_size:
                output = (
                    output[:max_size] + "\n[Output truncated - size limit exceeded]"
                )

            # Remove dangerous control characters but keep newlines
            output = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", output)

            return output.strip()

        except Exception as e:
            logger.error(f"Failed to sanitize execution output: {e}")
            return "[Output sanitization failed]"

    @classmethod
    def validate_generated_file(cls, file_info: Dict[str, Any]) -> bool:
        """Validate generated file for security.

        Args:
            file_info: Dictionary with path, size, and mime_type

        Returns:
            True if file is safe to return, False otherwise
        """
        try:
            # Check file size
            if file_info.get("size", 0) > settings.max_file_size_mb * 1024 * 1024:
                logger.warning(
                    f"Generated file {file_info.get('path')} exceeds size limit"
                )
                return False

            file_path = file_info.get("path", "")

            # Handle absolute paths from container workspace
            container_workspace = "/mnt/data/"
            if file_path.startswith(container_workspace):
                relative_path = file_path[len(container_workspace) :]
            else:
                relative_path = file_path

            # Check for path traversal attempts
            if ".." in relative_path or (
                relative_path.startswith("/")
                and not file_path.startswith(container_workspace)
            ):
                logger.warning(f"Generated file {file_path} has suspicious path")
                return False

            # Check file using centralized settings validation
            filename = Path(file_path).name
            if not settings.is_file_allowed(filename):
                logger.warning(f"Generated file {file_path} has blocked extension")
                return False

            return True

        except Exception as e:
            logger.error(f"Failed to validate generated file: {e}")
            return False

    @classmethod
    def guess_mime_type(cls, filename: str) -> str:
        """Guess MIME type from filename.

        Args:
            filename: File name or path

        Returns:
            MIME type string
        """
        extension = Path(filename).suffix.lower()
        return cls.MIME_TYPES.get(extension, "application/octet-stream")

    @classmethod
    def determine_execution_status(
        cls, exit_code: int, stderr: str, execution_time_ms: int
    ) -> ExecutionStatus:
        """Determine the final execution status based on various factors.

        Args:
            exit_code: Process exit code
            stderr: Standard error output
            execution_time_ms: Execution time in milliseconds

        Returns:
            ExecutionStatus enum value
        """
        # Check for timeout (exit code 124 is timeout from timeout command)
        if exit_code == 124:
            return ExecutionStatus.TIMEOUT

        # Check for successful execution
        if exit_code == 0:
            return ExecutionStatus.COMPLETED

        # Check for specific error conditions in stderr
        if stderr:
            stderr_lower = stderr.lower()

            # Memory-related errors
            if any(
                term in stderr_lower
                for term in ["out of memory", "memory error", "segmentation fault"]
            ):
                logger.warning("Execution failed due to memory issues")
                return ExecutionStatus.FAILED

            # Permission-related errors
            if any(
                term in stderr_lower for term in ["permission denied", "access denied"]
            ):
                logger.warning("Execution failed due to permission issues")
                return ExecutionStatus.FAILED

        # Check execution time for potential issues
        if execution_time_ms > settings.max_execution_time * 1000 * 0.9:
            logger.warning("Execution took close to timeout limit")

        # Default to failed for non-zero exit codes
        return ExecutionStatus.FAILED

    @classmethod
    def format_error_message(cls, exit_code: int, stderr: str) -> str:
        """Format a user-friendly error message.

        Args:
            exit_code: Process exit code
            stderr: Standard error output

        Returns:
            Formatted error message
        """
        if exit_code == 124:
            return "Code execution timed out"

        if not stderr:
            return f"Code execution failed with exit code {exit_code}"

        # Clean up stderr for user display
        stderr_clean = cls.sanitize_output(stderr)
        stderr_lower = stderr_clean.lower()

        # Permission-related errors
        if "permission denied" in stderr_lower:
            return "File permission error occurred during execution. Please try again."

        # Java compilation errors
        if (
            "javac: not found" in stderr_lower
            or "javac: command not found" in stderr_lower
        ):
            return "Java compilation not supported. Please use simple Java code that doesn't require compilation."

        # Memory-related errors
        if any(term in stderr_lower for term in ["out of memory", "memory error"]):
            return "Code execution failed due to memory limitations. Please reduce memory usage."

        # Network-related errors
        if any(
            term in stderr_lower
            for term in [
                "network unreachable",
                "connection refused",
                "name resolution failed",
            ]
        ):
            return "Network access is not available in the execution environment for security reasons."

        # Truncate very long error messages
        if len(stderr_clean) > 500:
            stderr_clean = stderr_clean[:500] + "...\n[Error message truncated]"

        return f"Execution failed (exit code {exit_code}):\n{stderr_clean}"

    @classmethod
    def sanitize_filename(cls, input_name: str) -> str:
        """Sanitize filename to match LibreChat's sanitization logic.

        Replaces all non-alphanumeric characters (except '.' and '-') with
        underscores. This ensures filenames on disk match what LibreChat
        reports in the system prompt.

        Args:
            input_name: Original filename (may include path components)

        Returns:
            Sanitized filename safe for container use
        """
        if not input_name:
            return "_"

        try:
            # Remove any directory components (path traversal prevention)
            name = os.path.basename(input_name)

            # Replace any non-alphanumeric characters except for '.' and '-'
            name = re.sub(r"[^a-zA-Z0-9.-]", "_", name)

            # Ensure the name doesn't start with a dot (hidden file in Unix)
            if name.startswith(".") or name == "":
                name = "_" + name

            # Limit the length of the filename
            max_length = 255
            if len(name) > max_length:
                ext = os.path.splitext(name)[1]
                name_without_ext = os.path.splitext(name)[0]
                random_suffix = secrets.token_hex(3)
                truncate_len = max_length - len(ext) - 7
                if truncate_len < 1:
                    truncate_len = 1
                name = name_without_ext[:truncate_len] + "-" + random_suffix + ext

            return name

        except Exception as e:
            logger.error(f"Failed to sanitize filename: {e}")
            return "_"
