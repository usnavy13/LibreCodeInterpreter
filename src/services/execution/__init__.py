"""Code execution services.

This package provides code execution functionality split into:
- runner.py: Core execution logic
- output.py: Output processing and validation
"""

from .runner import CodeExecutionRunner
from .output import OutputProcessor

# Backward compatibility: CodeExecutionService is an alias for CodeExecutionRunner
# that implements the ExecutionServiceInterface
from ..interfaces import ExecutionServiceInterface
from .runner import CodeExecutionRunner as _Runner
from ..container import ContainerManager


class CodeExecutionService(_Runner, ExecutionServiceInterface):
    """Service for executing code in Docker containers.

    This class provides backward compatibility with the original
    CodeExecutionService API while using the refactored implementation.
    """

    async def execute_code(
        self, session_id, request, files=None, initial_state=None, capture_state=True
    ):
        """Execute code in a session (implements ExecutionServiceInterface).

        Args:
            session_id: Session identifier
            request: ExecuteCodeRequest with code and language
            files: Optional list of files to mount
            initial_state: Base64-encoded state to restore (Python only)
            capture_state: Whether to capture state after execution (Python only)

        Returns:
            Tuple of (CodeExecution, Container, new_state, state_errors)
            Container returned directly for thread-safe file retrieval in concurrent requests.
            new_state is base64-encoded cloudpickle, or None if not captured.
        """
        return await self.execute(
            session_id, request, files, initial_state, capture_state
        )

    def _normalize_container_filename(self, filename):
        """Backward compatibility alias."""
        return OutputProcessor.sanitize_filename(filename)

    def _sanitize_execution_output(self, output):
        """Backward compatibility alias."""
        return OutputProcessor.sanitize_output(output)

    def _validate_generated_file(self, file_info):
        """Backward compatibility alias."""
        return OutputProcessor.validate_generated_file(file_info)

    def _guess_mime_type(self, filename):
        """Backward compatibility alias."""
        return OutputProcessor.guess_mime_type(filename)

    def _determine_execution_status(self, exit_code, stderr, execution_time_ms):
        """Backward compatibility alias."""
        return OutputProcessor.determine_execution_status(
            exit_code, stderr, execution_time_ms
        )

    def _format_error_message(self, exit_code, stderr):
        """Backward compatibility alias."""
        return OutputProcessor.format_error_message(exit_code, stderr)

    def __del__(self):
        """Cleanup when service is destroyed."""
        try:
            self.container_manager.close()
        except Exception:
            pass


__all__ = [
    "CodeExecutionService",
    "CodeExecutionRunner",
    "OutputProcessor",
    "ContainerManager",
]
