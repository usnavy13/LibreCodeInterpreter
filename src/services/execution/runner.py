"""Code execution runner - core execution logic."""

import asyncio
import os
import shlex
import signal
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import structlog

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
from ..sandbox.nsjail import SandboxInfo
from ..sandbox.manager import SandboxManager
from ..sandbox.pool import SandboxPool
from ..sandbox.repl_executor import SandboxREPLExecutor, SandboxREPLProcess
from .output import OutputProcessor

logger = structlog.get_logger(__name__)


class CodeExecutionRunner:
    """Core code execution runner."""

    def __init__(
        self,
        sandbox_manager: SandboxManager = None,
        sandbox_pool: SandboxPool = None,
    ):
        """Initialize the execution runner.

        Args:
            sandbox_manager: Optional sandbox manager instance
            sandbox_pool: Optional sandbox pool for fast sandbox acquisition
        """
        self.sandbox_manager = sandbox_manager or SandboxManager()
        self.sandbox_pool = sandbox_pool
        self.active_executions: Dict[str, CodeExecution] = {}
        self.session_sandboxes: Dict[str, SandboxInfo] = {}
        self._repl_processes: Dict[str, SandboxREPLProcess] = {}

    def set_sandbox_pool(self, pool: SandboxPool) -> None:
        """Set the sandbox pool dependency."""
        self.sandbox_pool = pool

    async def _get_sandbox(
        self, session_id: str, language: str
    ) -> Tuple[SandboxInfo, str]:
        """Get sandbox for execution, using pool if available.

        Priority:
        1. Get fresh sandbox from pool (fast, ~3ms)
        2. Create new sandbox (fallback, slow)

        Returns:
            Tuple of (SandboxInfo, source) where source is 'pool_hit' or 'pool_miss'
        """
        # Try pool first if enabled
        if self.sandbox_pool and settings.sandbox_pool_enabled:
            logger.debug(
                "Acquiring sandbox from pool",
                session_id=session_id[:12],
                pool_enabled=True,
            )
            try:
                sandbox_info = await self.sandbox_pool.acquire(language, session_id)
                return sandbox_info, "pool_hit"
            except Exception as e:
                logger.warning(
                    "Pool acquire failed, falling back to fresh sandbox",
                    session_id=session_id[:12],
                    error=str(e),
                )
        else:
            logger.debug(
                "Pool not available",
                has_pool=self.sandbox_pool is not None,
                pool_enabled=settings.sandbox_pool_enabled,
            )

        # Fallback: create fresh sandbox (original behavior)
        sandbox_info = await self._create_fresh_sandbox(session_id, language)
        return sandbox_info, "pool_miss"

    async def execute(
        self,
        session_id: str,
        request: ExecuteCodeRequest,
        files: Optional[List[Dict[str, Any]]] = None,
        initial_state: Optional[str] = None,
        capture_state: bool = True,
    ) -> Tuple[CodeExecution, Optional[SandboxInfo], Optional[str], List[str], str]:
        """Execute code in a session with optional state persistence.

        Args:
            session_id: Session identifier
            request: Execution request with code and language
            files: Optional list of files to mount
            initial_state: Base64-encoded state to restore before execution (Python only)
            capture_state: Whether to capture state after execution (Python only)

        Returns:
            Tuple of (CodeExecution record, SandboxInfo, new_state, state_errors, container_source)
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

        # Check if sandbox/nsjail is available
        if not self.sandbox_manager.is_available():
            logger.error(
                "Sandbox/nsjail not available",
                execution_id=execution_id[:8],
                error=self.sandbox_manager.get_initialization_error(),
            )
            execution.status = ExecutionStatus.FAILED
            execution.completed_at = datetime.utcnow()
            execution.error_message = f"Sandbox service unavailable: {self.sandbox_manager.get_initialization_error()}"
            return execution, None, None, [], "pool_miss"

        sandbox_info = None
        container_source = "pool_miss"
        try:
            execution.status = ExecutionStatus.RUNNING
            execution.started_at = datetime.utcnow()

            # Get sandbox (from pool or create fresh)
            sandbox_info, container_source = await self._get_sandbox(
                session_id, request.language
            )

            # Mount files if provided
            if files:
                await self._mount_files_to_sandbox(
                    sandbox_info, files, request.language
                )

            # Execute the code
            start_time = datetime.utcnow()

            # Check if this is a REPL sandbox (for optimization)
            is_repl = self._is_repl_sandbox(sandbox_info, request.language)

            # nsjail doesn't expose detailed per-sandbox resource stats
            initial_stats = None

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
                    sandbox_info,
                    request.code,
                    request.timeout or settings.max_execution_time,
                    initial_state=initial_state,
                    capture_state=capture_state,
                    args=request.args,
                )
            else:
                # Standard execution (no state persistence)
                exit_code, stdout, stderr = await self._execute_code_in_sandbox(
                    sandbox_info,
                    request.code,
                    request.language,
                    request.timeout,
                    args=request.args,
                )
            end_time = datetime.utcnow()

            execution_time_ms = int((end_time - start_time).total_seconds() * 1000)

            # nsjail doesn't provide per-sandbox memory stats
            memory_peak_mb = None

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
                generated_files = await self._detect_generated_files(sandbox_info)

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

        return execution, sandbox_info, new_state, state_errors, container_source

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

    async def _create_fresh_sandbox(
        self, session_id: str, language: str
    ) -> SandboxInfo:
        """Create a fresh sandbox for execution."""
        if session_id in self.session_sandboxes:
            try:
                old_sandbox = self.session_sandboxes[session_id]
                # Kill any REPL process
                repl_proc = self._repl_processes.pop(old_sandbox.sandbox_id, None)
                if repl_proc and repl_proc.process.returncode is None:
                    try:
                        os.killpg(repl_proc.process.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        try:
                            repl_proc.process.kill()
                        except ProcessLookupError:
                            pass
                    try:
                        await repl_proc.process.wait()
                    except Exception:
                        pass
                self.sandbox_manager.destroy_sandbox(old_sandbox)
            except Exception:
                pass
            finally:
                if session_id in self.session_sandboxes:
                    del self.session_sandboxes[session_id]

        # Enable REPL mode for Python if configured (matches pool behavior)
        use_repl_mode = language == "py" and settings.repl_enabled

        sandbox_info = self.sandbox_manager.create_sandbox(
            session_id=session_id,
            language=language,
            repl_mode=use_repl_mode,
        )

        # For REPL sandboxes, start the REPL process and wait for ready
        if use_repl_mode:
            repl_process = await self._start_repl_process(sandbox_info)
            if repl_process:
                self._repl_processes[sandbox_info.sandbox_id] = repl_process
            else:
                logger.warning(
                    "REPL not ready in fresh sandbox, may affect performance",
                    session_id=session_id[:12],
                    sandbox_id=sandbox_info.sandbox_id[:12],
                )

        self.session_sandboxes[session_id] = sandbox_info
        logger.info(
            "Fresh sandbox created",
            session_id=session_id,
            sandbox_id=sandbox_info.sandbox_id[:12],
        )
        return sandbox_info

    async def _start_repl_process(
        self, sandbox_info: SandboxInfo
    ) -> Optional[SandboxREPLProcess]:
        """Start a REPL process inside an nsjail sandbox.

        Args:
            sandbox_info: Sandbox to start REPL in

        Returns:
            SandboxREPLProcess if successful, None if failed
        """
        try:
            from ..sandbox.nsjail import NsjailConfig

            nsjail_config = NsjailConfig()

            # Build nsjail args for REPL mode
            env = self.sandbox_manager.executor._build_sanitized_env("py")
            nsjail_args = nsjail_config.build_args(
                sandbox_dir=str(sandbox_info.data_dir),
                command=["/usr/bin/python3", "/opt/repl_server.py"],
                language="py",
                repl_mode=True,
                env=env,
            )

            # Wrap nsjail in unshare+mount for security isolation
            nsjail_cmd = " ".join(
                shlex.quote(str(a)) for a in [settings.nsjail_binary] + nsjail_args
            )
            wrapper_cmd = (
                f"mount --bind {shlex.quote(str(sandbox_info.data_dir))} /mnt/data && "
                f"mount -t tmpfs -o size=1k tmpfs /var/lib/code-interpreter/sandboxes && "
                f"mount -t tmpfs -o size=1k tmpfs /app/data && "
                f"mount -t tmpfs -o size=1k tmpfs /var/log && "
                f"mount -t tmpfs -o size=1k tmpfs /app/ssl && "
                f"mount -t tmpfs -o size=1k tmpfs /app/dashboard && "
                f"mount -t tmpfs -o size=1k tmpfs /app/src && "
                # BUG-003: Bind /dev/null over mountinfo to hide mount details
                f"mount --bind /dev/null /proc/self/mountinfo && "
                f"{nsjail_cmd}"
            )

            # Start the nsjail subprocess with REPL via unshare wrapper
            proc = await asyncio.create_subprocess_exec(
                "unshare",
                "--mount",
                "--",
                "/bin/sh",
                "-c",
                wrapper_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            repl_process = SandboxREPLProcess(
                process=proc,
                sandbox_info=sandbox_info,
            )

            # Wait for REPL to be ready
            repl_executor = SandboxREPLExecutor()
            ready = await repl_executor.wait_for_ready(
                repl_process,
                timeout=settings.repl_warmup_timeout_seconds,
            )

            if not ready:
                proc.kill()
                await proc.wait()
                return None

            return repl_process

        except Exception as e:
            logger.error(
                "Failed to start REPL process",
                sandbox_id=sandbox_info.sandbox_id[:12],
                error=str(e),
            )
            return None

    async def _execute_code_in_sandbox(
        self,
        sandbox_info: SandboxInfo,
        code: str,
        language: str,
        timeout: Optional[int] = None,
        args: Optional[List[str]] = None,
    ) -> Tuple[int, str, str]:
        """Execute code in the sandbox.

        For REPL-enabled sandboxes (Python with REPL mode), uses the fast
        REPL executor which communicates with the pre-warmed Python interpreter.
        For other sandboxes, uses the standard execution path.

        Args:
            sandbox_info: Sandbox to execute in
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

        # Check if sandbox is REPL-enabled for faster execution
        if self._is_repl_sandbox(sandbox_info, language):
            logger.debug(
                "Using REPL executor",
                sandbox_id=sandbox_info.sandbox_id[:12],
                language=language,
            )
            return await self._execute_via_repl(
                sandbox_info, code, execution_timeout, args=args
            )

        # Standard execution path for non-REPL sandboxes
        exec_command = lang_config.execution_command

        # For stdin-based languages (except ts which compiles first)
        if lang_config.uses_stdin and language != "ts":
            return await self.sandbox_manager.execute_command(
                sandbox_info,
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

        # Direct memory-to-sandbox transfer (no tempfiles)
        dest_path = f"/mnt/data/{code_filename}"
        if not self.sandbox_manager.copy_content_to_sandbox(
            sandbox_info, code.encode("utf-8"), dest_path, language=language
        ):
            return 1, "", "Failed to write code file to sandbox"

        # Build execution command with args if provided
        final_command = exec_command
        if args:
            # Safely quote each argument to prevent shell injection
            quoted_args = " ".join(shlex.quote(arg) for arg in args)
            final_command = f"{exec_command} {quoted_args}"

        return await self.sandbox_manager.execute_command(
            sandbox_info,
            final_command,
            timeout=execution_timeout,
            language=language,
        )

    def _is_repl_sandbox(self, sandbox_info: SandboxInfo, language: str) -> bool:
        """Check if sandbox is running in REPL mode.

        Args:
            sandbox_info: Sandbox to check
            language: Programming language

        Returns:
            True if sandbox has REPL mode enabled, False otherwise
        """
        # Only Python supports REPL mode currently
        if language != "py":
            return False

        # Check if REPL is enabled in settings
        if not settings.repl_enabled:
            return False

        return sandbox_info.repl_mode

    async def _execute_via_repl(
        self,
        sandbox_info: SandboxInfo,
        code: str,
        timeout: int,
        args: Optional[List[str]] = None,
    ) -> Tuple[int, str, str]:
        """Execute code via REPL server in sandbox.

        Args:
            sandbox_info: Sandbox with REPL server running
            code: Python code to execute
            timeout: Maximum execution time in seconds
            args: Optional list of command line arguments

        Returns:
            Tuple of (exit_code, stdout, stderr)
        """
        # Get REPL process: try pool first, then local tracking
        repl_process = None
        if self.sandbox_pool:
            repl_process = self.sandbox_pool.get_repl_process(sandbox_info)
        if not repl_process:
            repl_process = self._repl_processes.get(sandbox_info.sandbox_id)

        if not repl_process:
            logger.warning(
                "No REPL process found for sandbox",
                sandbox_id=sandbox_info.sandbox_id[:12],
            )
            return 1, "", "REPL process not available"

        repl_executor = SandboxREPLExecutor()
        return await repl_executor.execute(
            repl_process, code, timeout=timeout, working_dir="/mnt/data", args=args
        )

    async def _execute_via_repl_with_state(
        self,
        sandbox_info: SandboxInfo,
        code: str,
        timeout: int,
        initial_state: Optional[str] = None,
        capture_state: bool = True,
        args: Optional[List[str]] = None,
    ) -> Tuple[int, str, str, Optional[str], List[str]]:
        """Execute code via REPL server with state persistence.

        Args:
            sandbox_info: Sandbox with REPL server running
            code: Python code to execute
            timeout: Maximum execution time in seconds
            initial_state: Base64-encoded state to restore before execution
            capture_state: Whether to capture state after execution
            args: Optional list of command line arguments

        Returns:
            Tuple of (exit_code, stdout, stderr, new_state, state_errors)
        """
        # Get REPL process: try pool first, then local tracking
        repl_process = None
        if self.sandbox_pool:
            repl_process = self.sandbox_pool.get_repl_process(sandbox_info)
        if not repl_process:
            repl_process = self._repl_processes.get(sandbox_info.sandbox_id)

        if not repl_process:
            logger.warning(
                "No REPL process found for sandbox",
                sandbox_id=sandbox_info.sandbox_id[:12],
            )
            return 1, "", "REPL process not available", None, []

        repl_executor = SandboxREPLExecutor()
        return await repl_executor.execute_with_state(
            repl_process,
            code,
            timeout=timeout,
            working_dir="/mnt/data",
            initial_state=initial_state,
            capture_state=capture_state,
            args=args,
        )

    async def _mount_files_to_sandbox(
        self,
        sandbox_info: SandboxInfo,
        files: List[Dict[str, Any]],
        language: str = "py",
    ) -> None:
        """Mount files to sandbox workspace."""
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
                        # Direct memory-to-sandbox transfer (no tempfiles)
                        normalized_filename = OutputProcessor.sanitize_filename(
                            filename
                        )
                        dest_path = f"/mnt/data/{normalized_filename}"

                        if self.sandbox_manager.copy_content_to_sandbox(
                            sandbox_info, file_content, dest_path, language=language
                        ):
                            logger.info(
                                "Mounted file",
                                filename=filename,
                                size=len(file_content),
                            )
                        else:
                            logger.warning("Failed to mount file", filename=filename)
                            await self._create_placeholder_file(sandbox_info, filename)
                    else:
                        logger.warning(
                            f"Could not retrieve content for file {filename}"
                        )
                        await self._create_placeholder_file(sandbox_info, filename)

                except Exception as file_error:
                    logger.error(f"Error retrieving file {filename}: {file_error}")
                    await self._create_placeholder_file(sandbox_info, filename)

        except Exception as e:
            logger.error(f"Failed to mount files to sandbox: {e}")

    async def _create_placeholder_file(
        self, sandbox_info: SandboxInfo, filename: str
    ) -> None:
        """Create a placeholder file when content cannot be retrieved."""
        try:
            normalized_filename = OutputProcessor.sanitize_filename(filename)
            placeholder = f"# File: {filename}\n# This is a placeholder - original file could not be retrieved\n"
            self.sandbox_manager.copy_content_to_sandbox(
                sandbox_info,
                placeholder.encode(),
                f"/mnt/data/{normalized_filename}",
                "py",
            )
        except Exception as e:
            logger.error(f"Failed to create placeholder file: {e}")

    async def _detect_generated_files(
        self, sandbox_info: SandboxInfo
    ) -> List[Dict[str, Any]]:
        """Detect files generated during execution."""
        try:
            generated_files = []
            data_dir = sandbox_info.data_dir

            if not data_dir.exists():
                return []

            for name in os.listdir(data_dir):
                # Skip code files
                if name.startswith("code") or name.startswith("Code."):
                    continue

                filepath = data_dir / name
                if filepath.is_file():
                    size = filepath.stat().st_size
                    if size <= settings.max_file_size_mb * 1024 * 1024:
                        generated_files.append(
                            {
                                "path": f"/mnt/data/{name}",
                                "size": size,
                                "mime_type": OutputProcessor.guess_mime_type(name),
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
            sandbox_info = self.session_sandboxes.get(execution.session_id)
            if sandbox_info:
                # Kill any REPL process
                repl_proc = self._repl_processes.pop(sandbox_info.sandbox_id, None)
                if repl_proc and repl_proc.process.returncode is None:
                    try:
                        os.killpg(repl_proc.process.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        try:
                            repl_proc.process.kill()
                        except ProcessLookupError:
                            pass
                    try:
                        await repl_proc.process.wait()
                    except Exception:
                        pass
                self.sandbox_manager.destroy_sandbox(sandbox_info)
                del self.session_sandboxes[execution.session_id]

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
            if session_id in self.session_sandboxes:
                sandbox_info = self.session_sandboxes[session_id]
                # Kill any REPL process
                repl_proc = self._repl_processes.pop(sandbox_info.sandbox_id, None)
                if repl_proc and repl_proc.process.returncode is None:
                    try:
                        os.killpg(repl_proc.process.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        try:
                            repl_proc.process.kill()
                        except ProcessLookupError:
                            pass
                    try:
                        await repl_proc.process.wait()
                    except Exception:
                        pass
                self.sandbox_manager.destroy_sandbox(sandbox_info)
                del self.session_sandboxes[session_id]

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

    async def cleanup_all_sandboxes(self) -> None:
        """Clean up all active sandboxes during shutdown."""
        logger.info("Cleaning up all sandboxes", count=len(self.session_sandboxes))

        # Kill all REPL processes
        for sandbox_id, repl_proc in list(self._repl_processes.items()):
            try:
                if repl_proc.process.returncode is None:
                    repl_proc.process.kill()
                    await repl_proc.process.wait()
            except Exception:
                pass
        self._repl_processes.clear()

        # Destroy all sandboxes
        cleaned = 0
        for session_id, sandbox_info in list(self.session_sandboxes.items()):
            try:
                self.sandbox_manager.destroy_sandbox(sandbox_info)
                cleaned += 1
            except Exception:
                pass

        logger.info(f"Cleaned up {cleaned}/{len(self.session_sandboxes)} sandboxes")

        self.session_sandboxes.clear()
        self.active_executions.clear()
