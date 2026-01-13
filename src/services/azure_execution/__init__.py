"""Azure Execution Service for Azure Container Apps deployment.

This service calls the executor HTTP service instead of spawning Docker containers.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx
import structlog

from ..interfaces import ExecutionServiceInterface
from ...config import settings
from ...config.azure import azure_settings
from ...models import CodeExecution, ExecuteCodeRequest, ExecutionOutput

logger = structlog.get_logger(__name__)


class AzureExecutionService(ExecutionServiceInterface):
    """
    Execution service that calls the executor HTTP service.

    In Azure Container Apps deployment, code execution happens in a separate
    container app (the executor service) rather than via Docker containers.
    """

    def __init__(
        self,
        executor_url: Optional[str] = None,
        timeout: int = 60,
        max_retries: int = 3,
    ):
        """
        Initialize the Azure execution service.

        Args:
            executor_url: URL of the executor service (e.g., http://executor:8001)
            timeout: HTTP request timeout in seconds
            max_retries: Maximum number of retries for failed requests
        """
        self.executor_url = executor_url or azure_settings.executor_url
        if not self.executor_url:
            raise ValueError(
                "Executor URL required. Set EXECUTOR_URL environment variable."
            )

        self.timeout = timeout or azure_settings.executor_timeout
        self.max_retries = max_retries or azure_settings.executor_max_retries

        # Create HTTP client with connection pooling
        self.client = httpx.AsyncClient(
            base_url=self.executor_url,
            timeout=httpx.Timeout(timeout=float(self.timeout)),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )

        # Track executions for cancel support
        self._executions: Dict[str, Dict[str, Any]] = {}

        logger.info(
            "Initialized Azure execution service",
            executor_url=self.executor_url,
            timeout=self.timeout,
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
        Execute code via the executor HTTP service.

        Args:
            session_id: Session identifier
            request: Execution request with code, language, timeout
            files: Optional list of files to mount
            initial_state: Optional base64-encoded Python state
            capture_state: Whether to capture Python state after execution

        Returns:
            Tuple of (CodeExecution, None, new_state, state_errors, source)
            Note: Container is always None since we're not using Docker
        """
        import uuid
        from datetime import datetime

        execution_id = str(uuid.uuid4())

        # Build request payload
        payload = {
            "code": request.code,
            "language": request.language,
            "timeout": request.timeout or 30,
            "session_id": session_id,
            "files": self._prepare_files(files) if files else None,
            "initial_state": initial_state,
            "capture_state": capture_state,
        }

        # Track execution
        self._executions[execution_id] = {
            "session_id": session_id,
            "language": request.language,
            "started_at": datetime.utcnow(),
            "status": "running",
        }

        try:
            # Call executor service
            response = await self._call_executor(payload)

            # Parse response
            exit_code = response.get("exit_code", 0)
            stdout = response.get("stdout", "")
            stderr = response.get("stderr", "")
            execution_time_ms = int(response.get("execution_time_ms", 0))
            new_state = response.get("state")
            state_errors = response.get("state_errors", [])
            timed_out = response.get("timed_out", False)
            error = response.get("error")

            # Determine status
            if timed_out:
                status = "timeout"
            elif exit_code != 0 or error:
                status = "failed"
            else:
                status = "completed"

            # Build execution outputs
            outputs = []
            if stdout:
                outputs.append(ExecutionOutput(type="stdout", content=stdout))
            if stderr:
                outputs.append(ExecutionOutput(type="stderr", content=stderr))

            # Handle generated files
            generated_files = response.get("generated_files", [])
            for file_info in generated_files:
                outputs.append(ExecutionOutput(
                    type="file",
                    content=file_info.get("path", file_info.get("filename", "")),
                ))

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
                started_at=self._executions[execution_id]["started_at"],
                completed_at=datetime.utcnow(),
                error_message=error or (stderr if status == "failed" else None),
            )

            # Update tracking
            self._executions[execution_id]["status"] = status
            self._executions[execution_id]["completed_at"] = datetime.utcnow()

            logger.info(
                "Execution completed",
                execution_id=execution_id[:8],
                session_id=session_id[:12] if session_id else None,
                language=request.language,
                status=status,
                execution_time_ms=execution_time_ms,
            )

            # Return in format compatible with existing orchestrator
            # (execution, container, new_state, state_errors, source)
            return execution, None, new_state, state_errors, "azure_executor"

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
                started_at=self._executions[execution_id]["started_at"],
                completed_at=datetime.utcnow(),
                error_message=str(e),
            )

            self._executions[execution_id]["status"] = "failed"
            self._executions[execution_id]["error"] = str(e)

            return execution, None, None, [str(e)], "azure_executor"

    async def _call_executor(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Call the executor service with retries."""
        last_error = None

        for attempt in range(self.max_retries):
            try:
                response = await self.client.post("/execute", json=payload)
                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError as e:
                last_error = e
                logger.warning(
                    "Executor request failed",
                    status_code=e.response.status_code,
                    attempt=attempt + 1,
                )
                if e.response.status_code >= 500:
                    # Retry on server errors
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise

            except httpx.TimeoutException as e:
                last_error = e
                logger.warning(
                    "Executor request timed out",
                    attempt=attempt + 1,
                )
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise

            except Exception as e:
                last_error = e
                logger.warning(
                    "Executor request error",
                    error=str(e),
                    attempt=attempt + 1,
                )
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise

        raise last_error or Exception("Executor request failed after retries")

    def _prepare_files(self, files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Prepare files for executor request."""
        prepared = []
        for file_info in files:
            prepared.append({
                "file_id": file_info.get("file_id", ""),
                "filename": file_info.get("filename", ""),
                "path": file_info.get("path", ""),
            })
        return prepared

    async def get_execution(self, execution_id: str) -> Optional[CodeExecution]:
        """Retrieve an execution by ID (from local cache only)."""
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
        """Cancel a running execution (not fully supported in HTTP mode)."""
        if execution_id in self._executions:
            self._executions[execution_id]["status"] = "cancelled"
            logger.warning(
                "Execution cancel requested (may not interrupt in-flight request)",
                execution_id=execution_id[:8],
            )
            return True
        return False

    async def list_executions(
        self, session_id: str, limit: int = 100
    ) -> List[CodeExecution]:
        """List executions for a session (from local cache only)."""
        executions = []
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
        """Check if the executor service is healthy."""
        try:
            response = await self.client.get("/health")
            return response.status_code == 200
        except Exception as e:
            logger.warning("Executor health check failed", error=str(e))
            return False

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()
        logger.info("Closed Azure execution service")
