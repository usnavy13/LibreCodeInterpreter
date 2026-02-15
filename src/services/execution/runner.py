"""Code execution runner - core execution logic."""

import asyncio
import shlex
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import structlog
from docker.models.containers import Container

from ...config import settings
from ...config.languages import get_language
from ...models import (
    CodeExecution,
    ExecutionStatus,
    ExecutionOutput,
    OutputType,
    ExecuteCodeRequest,
)
from ...utils.id_generator import generate_execution_id
from ..container import ContainerManager
from ..container.pool import ContainerPool
from ..container.repl_executor import REPLExecutor
from .output import OutputProcessor

logger = structlog.get_logger(__name__)


class CodeExecutionRunner:
    """Core code execution runner."""

    def __init__(
        self,
        container_manager: ContainerManager = None,
        container_pool: ContainerPool = None,
    ):
        """Initialize the execution runner.

        Args:
            container_manager: Optional container manager instance
            container_pool: Optional container pool for fast container acquisition
        """
        self.container_manager = container_manager or ContainerManager()
        self.container_pool = container_pool
        self.active_executions: Dict[str, CodeExecution] = {}
        self.session_containers: Dict[str, Container] = {}

    def set_container_pool(self, pool: ContainerPool) -> None:
        """Set the container pool dependency."""
        self.container_pool = pool

    async def _get_container(
        self, session_id: str, language: str
    ) -> Tuple[Container, str]:
        """Get container for execution, using pool if available.

        Priority:
        1. Get fresh container from pool (fast, ~3ms)
        2. Create new container (fallback, slow)

        Returns:
            Tuple of (Container, source) where source is 'pool_hit' or 'pool_miss'
        """
        # Try pool first if enabled
        if self.container_pool and settings.container_pool_enabled:
            logger.debug(
                "Acquiring container from pool",
                session_id=session_id[:12],
                pool_enabled=True,
            )
            try:
                container = await self.container_pool.acquire(language, session_id)
                return container, "pool_hit"
            except Exception as e:
                logger.warning(
                    "Pool acquire failed, falling back to fresh container",
                    session_id=session_id[:12],
                    error=str(e),
                )
        else:
            logger.debug(
                "Pool not available",
                has_pool=self.container_pool is not None,
                pool_enabled=settings.container_pool_enabled,
            )

        # Fallback: create fresh container (original behavior)
        container = await self._create_fresh_container(session_id, language)
        return container, "pool_miss"

    async def execute(
        self,
        session_id: str,
        request: ExecuteCodeRequest,
        files: Optional[List[Dict[str, Any]]] = None,
        initial_state: Optional[str] = None,
        capture_state: bool = True,
    ) -> Tuple[CodeExecution, Optional[Container], Optional[str], List[str], str]:
        """Execute code in a session with optional state persistence.

        Args:
            session_id: Session identifier
            request: Execution request with code and language
            files: Optional list of files to mount
            initial_state: Base64-encoded state to restore before execution (Python only)
            capture_state: Whether to capture state after execution (Python only)

        Returns:
            Tuple of (CodeExecution record, Container, new_state, state_errors, container_source)
            container_source is 'pool_hit' or 'pool_miss'.
        """
        execution_id = generate_execution_id()

        logger.info(
            "Starting code execution",
            execution_id=execution_id[:8],
            session_id=session_id,
            language=request.language,
            code_length=len(request.code),
        )

        # Create execution record
        execution = CodeExecution(
            execution_id=execution_id,
            session_id=session_id,
            code=request.code,
            language=request.language,
            status=ExecutionStatus.PENDING,
        )

        self.active_executions[execution_id] = execution

        # Check if Docker is available
        if not self.container_manager.is_available():
            logger.error(
                "Docker not available",
                execution_id=execution_id[:8],
                error=self.container_manager.get_initialization_error(),
            )
            execution.status = ExecutionStatus.FAILED
            execution.completed_at = datetime.utcnow()
            execution.error_message = f"Docker service unavailable: {self.container_manager.get_initialization_error()}"
            return execution, None, None, [], "pool_miss"

        container = None
        container_source = "pool_miss"
        try:
            execution.status = ExecutionStatus.RUNNING
            execution.started_at = datetime.utcnow()

            # Get container (from pool or create fresh)
            container, container_source = await self._get_container(
                session_id, request.language
            )

            # Mount files if provided
            if files:
                await self._mount_files_to_container(container, files, request.language)

            # Execute the code
            start_time = datetime.utcnow()

            # Check if this is a REPL container (for optimization)
            is_repl = self._is_repl_container(container, request.language)

            # Skip stats for REPL mode (saves ~1 second per call)
            initial_stats = None
            if not is_repl:
                initial_stats = await self.container_manager.get_container_stats(
                    container
                )

            # Execute code with optional state persistence (Python REPL only)
            new_state = None
            state_errors: list[str] = []

            if is_repl and settings.state_persistence_enabled:
                # Use state-aware REPL execution
                (
                    exit_code,
                    stdout,
                    stderr,
                    new_state,
                    state_errors,
                ) = await self._execute_via_repl_with_state(
                    container,
                    request.code,
                    request.timeout or settings.max_execution_time,
                    initial_state=initial_state,
                    capture_state=capture_state,
                    args=request.args,
                )
            else:
                # Standard execution (no state persistence)
                exit_code, stdout, stderr = await self._execute_code_in_container(
                    container,
                    request.code,
                    request.language,
                    request.timeout,
                    args=request.args,
                )
            end_time = datetime.utcnow()

            execution_time_ms = int((end_time - start_time).total_seconds() * 1000)

            # Skip final stats for REPL mode
            memory_peak_mb = None
            if not is_repl:
                final_stats = await self.container_manager.get_container_stats(
                    container
                )
                memory_peak_mb = (
                    final_stats.get("memory_usage_mb") if final_stats else None
                )

            # Process outputs
            outputs = self._process_outputs(stdout, stderr, end_time)

            # For REPL mode without files, skip file detection (saves ~1 second)
            # Only detect files if code likely generates files (contains file-related calls)
            should_detect_files = (
                not is_repl
                or files
                or any(
                    kw in request.code
                    for kw in ["open(", "savefig", "to_csv", "write(", ".save("]
                )
            )

            generated_files = []
            if should_detect_files:
                generated_files = await self._detect_generated_files(container)

            mounted_filenames = self._get_mounted_filenames(files)
            filtered_files = self._filter_generated_files(
                generated_files, mounted_filenames
            )

            for file_info in filtered_files:
                if OutputProcessor.validate_generated_file(file_info):
                    outputs.append(
                        ExecutionOutput(
                            type=OutputType.FILE,
                            content=file_info["path"],
                            mime_type=file_info.get("mime_type"),
                            size=file_info.get("size"),
                            timestamp=end_time,
                        )
                    )

            # Update execution record
            execution.status = OutputProcessor.determine_execution_status(
                exit_code, stderr, execution_time_ms
            )
            execution.completed_at = end_time
            execution.outputs = outputs
            execution.exit_code = exit_code
            execution.execution_time_ms = execution_time_ms
            execution.memory_peak_mb = memory_peak_mb

            if execution.status == ExecutionStatus.FAILED:
                execution.error_message = OutputProcessor.format_error_message(
                    exit_code, stderr
                )

            logger.info(
                f"Code execution {execution_id} completed: status={execution.status}, "
                f"exit_code={exit_code}, time={execution_time_ms}ms, source={container_source}"
            )

            # Log state info if captured
            if new_state:
                logger.debug(
                    "State captured",
                    session_id=session_id[:12],
                    state_size=len(new_state),
                )
            if state_errors:
                for err in state_errors[:3]:  # Log first 3 errors
                    logger.debug("State serialization warning", warning=err)

        except asyncio.TimeoutError:
            execution.status = ExecutionStatus.TIMEOUT
            execution.completed_at = datetime.utcnow()
            execution.error_message = f"Execution timed out after {request.timeout or settings.max_execution_time} seconds"
            execution.execution_time_ms = (
                int((datetime.utcnow() - execution.started_at).total_seconds() * 1000)
                if execution.started_at
                else 0
            )
            new_state = None
            state_errors = []
            logger.warning(f"Code execution {execution_id} timed out")

        except Exception as e:
            execution.status = ExecutionStatus.FAILED
            execution.completed_at = datetime.utcnow()
            execution.error_message = str(e)
            execution.execution_time_ms = (
                int((datetime.utcnow() - execution.started_at).total_seconds() * 1000)
                if execution.started_at
                else 0
            )
            new_state = None
            state_errors = []
            logger.error(f"Code execution {execution_id} failed: {e}")

        return execution, container, new_state, state_errors, container_source

    def _process_outputs(
        self, stdout: str, stderr: str, timestamp: datetime
    ) -> List[ExecutionOutput]:
        """Process stdout and stderr into ExecutionOutput list."""
        outputs = []

        if stdout and stdout.strip():
            outputs.append(
                ExecutionOutput(
                    type=OutputType.STDOUT,
                    content=OutputProcessor.sanitize_output(stdout),
                    timestamp=timestamp,
                )
            )

        if stderr and stderr.strip():
            outputs.append(
                ExecutionOutput(
                    type=OutputType.STDERR,
                    content=OutputProcessor.sanitize_output(stderr),
                    timestamp=timestamp,
                )
            )

        return outputs

    def _get_mounted_filenames(self, files: Optional[List[Dict[str, Any]]]) -> set:
        """Get set of mounted filenames for filtering."""
        mounted = set()
        if files:
            try:
                for f in files:
                    name = f.get("filename") or f.get("name")
                    if name:
                        mounted.add(name)
                        mounted.add(OutputProcessor.sanitize_filename(name))
            except Exception:
                pass
        return mounted

    def _filter_generated_files(
        self, generated: List[Dict[str, Any]], mounted_filenames: set
    ) -> List[Dict[str, Any]]:
        """Filter out mounted files from generated files list."""
        return [
            f
            for f in generated
            if Path(f.get("path", "")).name not in mounted_filenames
        ]

    async def _create_fresh_container(
        self, session_id: str, language: str
    ) -> Container:
        """Create a fresh container for execution."""
        if session_id in self.session_containers:
            try:
                await self.container_manager.force_kill_container(
                    self.session_containers[session_id]
                )
            except Exception:
                pass
            finally:
                if session_id in self.session_containers:
                    del self.session_containers[session_id]

        image = self.container_manager.get_image_for_language(language)
        await self.container_manager.pull_image_if_needed(image)

        # Enable REPL mode for Python if configured (matches pool behavior)
        use_repl_mode = language == "py" and settings.repl_enabled

        container = self.container_manager.create_container(
            image=image,
            session_id=session_id,
            working_dir="/mnt/data",
            language=language,
            repl_mode=use_repl_mode,
        )
        await self.container_manager.start_container(container)

        # For REPL containers, wait for REPL to be ready before returning
        if use_repl_mode:
            repl_executor = REPLExecutor(self.container_manager.client)
            ready = await repl_executor.wait_for_ready(container, timeout=10.0)
            if not ready:
                logger.warning(
                    "REPL not ready in fresh container, may affect performance",
                    session_id=session_id[:12],
                    container_id=container.id[:12],
                )

        self.session_containers[session_id] = container
        logger.info(
            "Fresh container created",
            session_id=session_id,
            container_id=container.id[:12],
        )
        return container

    async def _execute_code_in_container(
        self,
        container: Container,
        code: str,
        language: str,
        timeout: Optional[int] = None,
        args: Optional[List[str]] = None,
    ) -> Tuple[int, str, str]:
        """Execute code in the container.

        For REPL-enabled containers (Python with REPL mode), uses the fast
        REPL executor which communicates with the pre-warmed Python interpreter.
        For other containers, uses the standard execution path.

        Args:
            container: Docker container to execute in
            code: Code to execute
            language: Programming language
            timeout: Execution timeout in seconds
            args: Optional list of command line arguments
        """
        language = language.lower()
        lang_config = get_language(language)
        if not lang_config:
            raise ValueError(f"Unsupported language: {language}")

        execution_timeout = timeout or settings.max_execution_time

        # Check if container is REPL-enabled for faster execution
        if self._is_repl_container(container, language):
            logger.debug(
                "Using REPL executor", container_id=container.id[:12], language=language
            )
            return await self._execute_via_repl(
                container, code, execution_timeout, args=args
            )

        # Standard execution path for non-REPL containers
        exec_command = lang_config.execution_command

        # For stdin-based languages (except ts which compiles first)
        if lang_config.uses_stdin and language != "ts":
            return await self.container_manager.execute_command(
                container,
                exec_command,
                timeout=execution_timeout,
                language=language,
                stdin_payload=code,
            )

        # For file-based languages
        extension = lang_config.file_extension
        code_filename = f"code.{extension}"
        if language == "java":
            code_filename = "Code.java"
        elif language == "ts":
            code_filename = "code.ts"

        # Direct memory-to-container transfer (no tempfiles)
        dest_path = f"/mnt/data/{code_filename}"
        if not await self.container_manager.copy_content_to_container(
            container, code.encode("utf-8"), dest_path, language=language
        ):
            return 1, "", "Failed to write code file to container"

        # Build execution command with args if provided
        final_command = exec_command
        if args:
            # Safely quote each argument to prevent shell injection
            quoted_args = " ".join(shlex.quote(arg) for arg in args)
            final_command = f"{exec_command} {quoted_args}"

        return await self.container_manager.execute_command(
            container,
            final_command,
            timeout=execution_timeout,
            language=language,
            working_dir="/mnt/data",
        )

    def _is_repl_container(self, container: Container, language: str) -> bool:
        """Check if container is running in REPL mode.

        Args:
            container: Docker container to check
            language: Programming language

        Returns:
            True if container has REPL mode enabled, False otherwise
        """
        # Only Python supports REPL mode currently
        if language != "py":
            return False

        # Check if REPL is enabled in settings
        if not settings.repl_enabled:
            return False

        try:
            # Check container labels for REPL mode (no reload needed - labels set at creation)
            labels = container.labels or {}
            return labels.get("com.code-interpreter.repl-mode") == "true"
        except Exception as e:
            logger.debug(
                "Error checking REPL mode", container_id=container.id[:12], error=str(e)
            )
            return False

    async def _execute_via_repl(
        self,
        container: Container,
        code: str,
        timeout: int,
        args: Optional[List[str]] = None,
    ) -> Tuple[int, str, str]:
        """Execute code via REPL server in container.

        Args:
            container: Docker container with REPL server running
            code: Python code to execute
            timeout: Maximum execution time in seconds
            args: Optional list of command line arguments

        Returns:
            Tuple of (exit_code, stdout, stderr)
        """
        repl_executor = REPLExecutor(self.container_manager.client)
        return await repl_executor.execute(
            container, code, timeout=timeout, working_dir="/mnt/data", args=args
        )

    async def _execute_via_repl_with_state(
        self,
        container: Container,
        code: str,
        timeout: int,
        initial_state: Optional[str] = None,
        capture_state: bool = True,
        args: Optional[List[str]] = None,
    ) -> Tuple[int, str, str, Optional[str], List[str]]:
        """Execute code via REPL server with state persistence.

        Args:
            container: Docker container with REPL server running
            code: Python code to execute
            timeout: Maximum execution time in seconds
            initial_state: Base64-encoded state to restore before execution
            capture_state: Whether to capture state after execution
            args: Optional list of command line arguments

        Returns:
            Tuple of (exit_code, stdout, stderr, new_state, state_errors)
        """
        repl_executor = REPLExecutor(self.container_manager.client)
        return await repl_executor.execute_with_state(
            container,
            code,
            timeout=timeout,
            working_dir="/mnt/data",
            initial_state=initial_state,
            capture_state=capture_state,
            args=args,
        )

    async def _mount_files_to_container(
        self, container: Container, files: List[Dict[str, Any]], language: str = "py"
    ) -> None:
        """Mount files to container workspace."""
        try:
            from ..file import FileService

            file_service = FileService()

            for file_info in files:
                filename = file_info.get("filename", "unknown")
                file_id = file_info.get("file_id")
                session_id = file_info.get("session_id")

                if not file_id or not session_id:
                    logger.warning(f"Missing file_id or session_id for file {filename}")
                    continue

                try:
                    file_content = await file_service.get_file_content(
                        session_id, file_id
                    )

                    if file_content is not None:
                        # Direct memory-to-container transfer (no tempfiles)
                        normalized_filename = OutputProcessor.sanitize_filename(
                            filename
                        )
                        dest_path = f"/mnt/data/{normalized_filename}"

                        if await self.container_manager.copy_content_to_container(
                            container, file_content, dest_path, language=language
                        ):
                            logger.info(
                                "Mounted file",
                                filename=filename,
                                size=len(file_content),
                            )
                        else:
                            logger.warning("Failed to mount file", filename=filename)
                            await self._create_placeholder_file(container, filename)
                    else:
                        logger.warning(
                            f"Could not retrieve content for file {filename}"
                        )
                        await self._create_placeholder_file(container, filename)

                except Exception as file_error:
                    logger.error(f"Error retrieving file {filename}: {file_error}")
                    await self._create_placeholder_file(container, filename)

        except Exception as e:
            logger.error(f"Failed to mount files to container: {e}")

    async def _create_placeholder_file(
        self, container: Container, filename: str
    ) -> None:
        """Create a placeholder file when content cannot be retrieved."""
        try:
            normalized_filename = OutputProcessor.sanitize_filename(filename)
            create_command = f"""cat > /mnt/data/{normalized_filename} << 'EOF'
# File: {filename}
# This is a placeholder - original file could not be retrieved
EOF"""
            await self.container_manager.execute_command(
                container, create_command, timeout=10
            )
        except Exception as e:
            logger.error(f"Failed to create placeholder file: {e}")

    async def _detect_generated_files(
        self, container: Container
    ) -> List[Dict[str, Any]]:
        """Detect files generated during execution."""
        try:
            exit_code, stdout, stderr = await self.container_manager.execute_command(
                container,
                "find /mnt/data -maxdepth 1 -type f -name '*' ! -name 'code' ! -name 'code.*' ! -name 'Code.*' -exec ls -la {} \\;",
                timeout=5,
            )

            if exit_code != 0 or not stdout.strip():
                return []

            generated_files = []
            for line in stdout.strip().split("\n"):
                if line.strip():
                    parts = line.split()
                    if len(parts) >= 9:
                        size = int(parts[4]) if parts[4].isdigit() else 0
                        filename = " ".join(parts[8:])

                        if size > settings.max_file_size_mb * 1024 * 1024:
                            continue

                        generated_files.append(
                            {
                                "path": filename,
                                "size": size,
                                "mime_type": OutputProcessor.guess_mime_type(filename),
                            }
                        )

                        if len(generated_files) >= settings.max_output_files:
                            break

            return generated_files

        except Exception as e:
            logger.error(f"Failed to detect generated files: {e}")
            return []

    async def get_execution(self, execution_id: str) -> Optional[CodeExecution]:
        """Retrieve an execution by ID."""
        return self.active_executions.get(execution_id)

    async def cancel_execution(self, execution_id: str) -> bool:
        """Cancel a running execution."""
        execution = self.active_executions.get(execution_id)
        if not execution or execution.status not in [
            ExecutionStatus.PENDING,
            ExecutionStatus.RUNNING,
        ]:
            return False

        try:
            container = self.session_containers.get(execution.session_id)
            if container:
                await self.container_manager.stop_container(container)
                await self.container_manager.remove_container(container)
                del self.session_containers[execution.session_id]

            execution.status = ExecutionStatus.CANCELLED
            execution.completed_at = datetime.utcnow()
            execution.error_message = "Execution cancelled by user"

            logger.info(f"Cancelled execution {execution_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to cancel execution {execution_id}: {e}")
            return False

    async def list_executions(
        self, session_id: str, limit: int = 100
    ) -> List[CodeExecution]:
        """List executions for a session."""
        executions = [
            e for e in self.active_executions.values() if e.session_id == session_id
        ]
        executions.sort(key=lambda x: x.created_at, reverse=True)
        return executions[:limit]

    async def cleanup_session(self, session_id: str) -> bool:
        """Clean up resources for a session."""
        try:
            if session_id in self.session_containers:
                container = self.session_containers[session_id]
                await self.container_manager.force_kill_container(container)
                del self.session_containers[session_id]

            execution_ids = [
                eid
                for eid, e in self.active_executions.items()
                if e.session_id == session_id
            ]
            for eid in execution_ids:
                del self.active_executions[eid]

            logger.info("Cleaned up session resources", session_id=session_id)
            return True

        except Exception as e:
            logger.error(f"Failed to cleanup session: {e}")
            return False

    async def cleanup_expired_executions(self, max_age_hours: int = 24) -> int:
        """Clean up old execution records."""
        cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
        expired = [
            eid
            for eid, e in self.active_executions.items()
            if e.created_at < cutoff
            and e.status
            in [
                ExecutionStatus.COMPLETED,
                ExecutionStatus.FAILED,
                ExecutionStatus.TIMEOUT,
                ExecutionStatus.CANCELLED,
            ]
        ]

        for eid in expired:
            del self.active_executions[eid]

        logger.info(f"Cleaned up {len(expired)} expired executions")
        return len(expired)

    async def cleanup_all_containers(self) -> None:
        """Clean up all active containers during shutdown."""
        logger.info("Cleaning up all containers", count=len(self.session_containers))

        containers = list(self.session_containers.values())
        if containers:
            cleaned = await self.container_manager.force_kill_containers_batch(
                containers
            )
            logger.info(f"Cleaned up {cleaned}/{len(containers)} containers")

        self.session_containers.clear()
        self.active_executions.clear()
