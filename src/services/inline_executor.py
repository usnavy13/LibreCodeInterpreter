"""Inline Execution Service for Unified Azure Container Apps deployment.

This service executes code in-process (no HTTP call to separate executor),
using a semaphore to ensure serialized execution (1 at a time per replica).

Key features:
- In-process execution using executor.runner
- Semaphore-based serialization for isolation
- Full cleanup between executions
- Compatible with ExecutionServiceInterface
"""

import asyncio
import base64
import logging
import os
import shutil
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import structlog

from ..config import settings
from ..models import CodeExecution, ExecuteCodeRequest, ExecutionOutput
from .interfaces import ExecutionServiceInterface

logger = structlog.get_logger(__name__)

# Working directory for code execution
WORKING_DIR_BASE = os.environ.get("WORKING_DIR_BASE", "/mnt/data")


class InlineExecutionService(ExecutionServiceInterface):
    """
    Execution service that runs code in-process with serialization.

    In unified Azure Container Apps deployment, code execution happens
    directly in the same container using a semaphore to ensure only
    one execution runs at a time (per replica).
    """

    def __init__(
        self,
        max_concurrent: int = 1,
        working_dir: str = WORKING_DIR_BASE,
        file_service: Optional[Any] = None,
    ):
        """
        Initialize the inline execution service.

        Args:
            max_concurrent: Maximum concurrent executions (default 1 for isolation)
            working_dir: Base working directory for execution
            file_service: File service for downloading file content
        """
        self.max_concurrent = max_concurrent
        self.working_dir = working_dir
        self.file_service = file_service

        # Semaphore for serialized execution
        self._semaphore = asyncio.Semaphore(max_concurrent)

        # Track active executions
        self._executions: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

        # Ensure working directory exists
        os.makedirs(self.working_dir, exist_ok=True)

        logger.info(
            "Initialized inline execution service",
            max_concurrent=max_concurrent,
            working_dir=working_dir,
        )

    async def execute_code(
        self,
        session_id: str,
        request: ExecuteCodeRequest,
        files: Optional[List[Dict[str, Any]]] = None,
        initial_state: Optional[str] = None,
        capture_state: bool = True,
    ) -> Tuple[CodeExecution, None, Optional[str], List[str], str]:
        """
        Execute code in-process with serialization.

        Args:
            session_id: Session identifier
            request: Execution request with code, language, timeout
            files: Optional list of files to mount
            initial_state: Optional base64-encoded Python state
            capture_state: Whether to capture Python state after execution

        Returns:
            Tuple of (CodeExecution, None, new_state, state_errors, source)
            Note: Container is always None since we're using inline execution
        """
        execution_id = str(uuid.uuid4())
        started_at = datetime.utcnow()

        # Track execution
        async with self._lock:
            self._executions[execution_id] = {
                "session_id": session_id,
                "language": request.language,
                "started_at": started_at,
                "status": "pending",
            }

        logger.info(
            "Starting inline execution",
            execution_id=execution_id[:8],
            session_id=session_id[:12] if session_id else None,
            language=request.language,
        )

        try:
            # Acquire semaphore for serialized execution
            async with self._semaphore:
                async with self._lock:
                    self._executions[execution_id]["status"] = "running"

                try:
                    # Prepare working directory (clean slate)
                    await self._prepare_working_dir()

                    # Write files to working directory
                    if files:
                        await self._write_files(files)

                    # Execute code
                    result = await self._execute(
                        code=request.code,
                        language=request.language,
                        timeout=request.timeout or 30,
                        initial_state=initial_state,
                        capture_state=capture_state,
                    )

                    # Parse result
                    exit_code = result.get("exit_code", 0)
                    stdout = result.get("stdout", "")
                    stderr = result.get("stderr", "")
                    execution_time_ms = int(result.get("execution_time_ms", 0))
                    new_state = result.get("state")
                    state_errors = result.get("state_errors", [])
                    timed_out = result.get("timed_out", False)
                    error = result.get("error")
                    generated_files = result.get("generated_files", [])

                    # Determine status
                    if timed_out:
                        status = "timeout"
                    elif exit_code != 0 or error:
                        status = "failed"
                    else:
                        status = "completed"

                    # Build outputs
                    outputs = []
                    if stdout:
                        outputs.append(ExecutionOutput(type="stdout", content=stdout))
                    if stderr:
                        outputs.append(ExecutionOutput(type="stderr", content=stderr))

                    # Handle generated files - include content_b64 in metadata
                    for file_info in generated_files:
                        output_kwargs = {
                            "type": "file",
                            "content": file_info.get("path", file_info.get("filename", "")),
                        }
                        if "content_b64" in file_info:
                            output_kwargs["metadata"] = {"content_b64": file_info["content_b64"]}
                        outputs.append(ExecutionOutput(**output_kwargs))

                    # Build execution record
                    execution = CodeExecution(
                        execution_id=execution_id,
                        session_id=session_id,
                        language=request.language,
                        code=request.code,
                        status=status,
                        exit_code=exit_code,
                        outputs=outputs,
                        execution_time_ms=execution_time_ms,
                        started_at=started_at,
                        completed_at=datetime.utcnow(),
                        error_message=error or (stderr if status == "failed" else None),
                    )

                    # Update tracking
                    async with self._lock:
                        self._executions[execution_id]["status"] = status
                        self._executions[execution_id]["completed_at"] = datetime.utcnow()

                    logger.info(
                        "Execution completed",
                        execution_id=execution_id[:8],
                        status=status,
                        execution_time_ms=execution_time_ms,
                    )

                    return execution, None, new_state, state_errors, "inline_executor"

                finally:
                    # ALWAYS clean up, even on failure
                    await self._cleanup_working_dir()

        except Exception as e:
            logger.exception(
                "Execution failed",
                execution_id=execution_id[:8],
                error=str(e),
            )

            # Build error execution record
            execution = CodeExecution(
                execution_id=execution_id,
                session_id=session_id,
                language=request.language,
                code=request.code,
                status="failed",
                exit_code=-1,
                outputs=[ExecutionOutput(type="stderr", content=str(e))],
                execution_time_ms=0,
                started_at=started_at,
                completed_at=datetime.utcnow(),
                error_message=str(e),
            )

            async with self._lock:
                self._executions[execution_id]["status"] = "failed"
                self._executions[execution_id]["error"] = str(e)

            return execution, None, None, [str(e)], "inline_executor"

    async def _prepare_working_dir(self) -> None:
        """Prepare working directory (ensure clean state)."""
        await self._cleanup_working_dir()
        os.makedirs(self.working_dir, exist_ok=True)
        logger.debug("Prepared working directory", path=self.working_dir)

    async def _cleanup_working_dir(self) -> None:
        """Complete cleanup of working directory for next execution."""
        try:
            for entry in os.scandir(self.working_dir):
                try:
                    if entry.is_dir():
                        shutil.rmtree(entry.path, ignore_errors=True)
                    else:
                        os.unlink(entry.path)
                except Exception as e:
                    logger.warning(
                        "Failed to clean up entry",
                        path=entry.path,
                        error=str(e),
                    )
        except FileNotFoundError:
            pass  # Directory doesn't exist, nothing to clean
        except Exception as e:
            logger.warning("Failed to clean working directory", error=str(e))

    async def _write_files(self, files: List[Dict[str, Any]]) -> None:
        """Write files to working directory."""
        for file_info in files:
            filename = file_info.get("filename", "")
            if not filename:
                continue

            filepath = os.path.join(self.working_dir, filename)

            # Try to get content
            content = None

            # Check for content_b64 (from prepared files)
            if "content_b64" in file_info:
                try:
                    content = base64.b64decode(file_info["content_b64"])
                except Exception as e:
                    logger.warning(
                        "Failed to decode base64 content",
                        filename=filename,
                        error=str(e),
                    )

            # Try to download from file service if we have session_id and file_id
            if content is None and self.file_service:
                session_id = file_info.get("session_id", "")
                file_id = file_info.get("file_id", "")
                if session_id and file_id:
                    try:
                        content = await self.file_service.get_file_content(
                            session_id, file_id
                        )
                    except Exception as e:
                        logger.warning(
                            "Failed to get file from storage",
                            file_id=file_id[:12] if file_id else None,
                            error=str(e),
                        )

            if content:
                with open(filepath, "wb") as f:
                    f.write(content)
                logger.debug(
                    "Wrote file to working directory",
                    filename=filename,
                    size=len(content),
                )

    async def _execute(
        self,
        code: str,
        language: str,
        timeout: int,
        initial_state: Optional[str] = None,
        capture_state: bool = True,
    ) -> Dict[str, Any]:
        """Execute code using the executor runner."""
        # Import executor modules
        from executor.runner import execute_code
        from executor.models import ExecuteRequest, FileReference

        # Build request
        exec_request = ExecuteRequest(
            code=code,
            language=language,
            timeout=timeout,
            initial_state=initial_state,
            capture_state=capture_state,
        )

        # Run execution in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        result = await execute_code(
            request=exec_request,
            working_dir=self.working_dir,
        )

        # Convert response to dict
        return {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "execution_time_ms": result.execution_time_ms,
            "state": result.state,
            "state_errors": result.state_errors,
            "timed_out": result.timed_out,
            "error": result.error,
            "generated_files": [
                {
                    "filename": f.get("filename", ""),
                    "path": f.get("path", ""),
                    "size": f.get("size", 0),
                    "content_b64": f.get("content_b64"),
                }
                for f in result.generated_files
            ],
        }

    async def get_execution(self, execution_id: str) -> Optional[CodeExecution]:
        """Retrieve an execution by ID (from local cache only)."""
        async with self._lock:
            if execution_id in self._executions:
                exec_info = self._executions[execution_id]
                return CodeExecution(
                    execution_id=execution_id,
                    session_id=exec_info.get("session_id", ""),
                    language=exec_info.get("language", ""),
                    code="",
                    status=exec_info.get("status", "unknown"),
                    exit_code=0,
                    outputs=[],
                    execution_time_ms=0,
                    started_at=exec_info.get("started_at"),
                    completed_at=exec_info.get("completed_at"),
                )
        return None

    async def cancel_execution(self, execution_id: str) -> bool:
        """Cancel a running execution (not fully supported in inline mode)."""
        async with self._lock:
            if execution_id in self._executions:
                self._executions[execution_id]["status"] = "cancelled"
                logger.warning(
                    "Execution cancel requested (may not interrupt in-flight execution)",
                    execution_id=execution_id[:8],
                )
                return True
        return False

    async def list_executions(
        self, session_id: str, limit: int = 100
    ) -> List[CodeExecution]:
        """List executions for a session (from local cache only)."""
        executions = []
        async with self._lock:
            for exec_id, exec_info in self._executions.items():
                if exec_info.get("session_id") == session_id:
                    executions.append(CodeExecution(
                        execution_id=exec_id,
                        session_id=session_id,
                        language=exec_info.get("language", ""),
                        code="",
                        status=exec_info.get("status", "unknown"),
                        exit_code=0,
                        outputs=[],
                        execution_time_ms=0,
                        started_at=exec_info.get("started_at"),
                        completed_at=exec_info.get("completed_at"),
                    ))
                    if len(executions) >= limit:
                        break
        return executions

    async def health_check(self) -> bool:
        """Check if the execution service is healthy."""
        # Inline executor is always healthy if we can acquire the semaphore
        try:
            # Quick check - can we access the working directory?
            os.makedirs(self.working_dir, exist_ok=True)
            return True
        except Exception as e:
            logger.warning("Inline executor health check failed", error=str(e))
            return False

    async def close(self) -> None:
        """Cleanup resources."""
        logger.info("Closing inline execution service")
        await self._cleanup_working_dir()

    # Property for compatibility with orchestrator
    @property
    def container_manager(self):
        """No container manager in inline mode."""
        return None
