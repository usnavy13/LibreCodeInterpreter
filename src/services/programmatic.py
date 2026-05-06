"""Programmatic Tool Calling (PTC) service.

Manages sandbox lifecycle for PTC executions where code can pause
to request external tool calls and resume with results.
"""

import asyncio
import json
import math
import os
import re
import shlex
import signal
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

import structlog

from ..config import settings
from ..models.programmatic import (
    PTCFileInput,
    PTCToolCall,
    PTCToolDefinition,
    PTCToolResult,
    ProgrammaticExecResponse,
)
from .interfaces import FileServiceInterface
from .sandbox.manager import SandboxManager
from .sandbox.nsjail import NsjailConfig, SandboxInfo

logger = structlog.get_logger(__name__)

# Protocol delimiter must match docker/ptc_server.py
PTC_DELIMITER = "\n---PTC_END---\n"

# Default timeout for paused contexts (seconds)
PTC_PAUSE_TIMEOUT = 300  # 5 minutes

# Maximum round trips per execution
PTC_MAX_ROUND_TRIPS = 50


@dataclass
class PausedContext:
    """Stores state for a paused PTC execution waiting for tool results."""

    sandbox_info: SandboxInfo
    process: asyncio.subprocess.Process
    session_id: str
    lang: str = "py"
    round_trip_count: int = 0
    timeout_handle: Optional[asyncio.TimerHandle] = None
    accumulated_stdout: str = ""
    accumulated_stderr: str = ""
    execution_deadline: float = 0.0
    execution_timeout_seconds: int = 0


class ProgrammaticService:
    """Manages PTC execution lifecycle.

    Creates nsjail sandboxes, runs ptc_server.py inside them, and
    manages the request/response protocol for tool calls.
    """

    def __init__(
        self,
        sandbox_manager: Optional[SandboxManager] = None,
        file_service: Optional[FileServiceInterface] = None,
    ):
        self._sandbox_manager = sandbox_manager or SandboxManager()
        self._nsjail_config = NsjailConfig()
        self._paused_contexts: Dict[str, PausedContext] = {}
        self._file_service = file_service

    async def start_execution(
        self,
        code: str,
        tools: List[PTCToolDefinition],
        session_id: str,
        timeout: Optional[int] = None,
        files: Optional[List[PTCFileInput]] = None,
        lang: str = "py",
    ) -> ProgrammaticExecResponse:
        """Start a new PTC execution.

        Creates an nsjail sandbox, copies the appropriate PTC server script
        into it, and starts execution with the provided code and tools.

        Args:
            code: Code to execute (Python or bash, depending on `lang`)
            tools: Tool definitions available to the code
            session_id: Session identifier
            timeout: Execution timeout in seconds
            files: Optional referenced prior-session files to mount in sandbox
            lang: PTC language. "py" runs ptc_server.py (asyncio + Python).
                "bash" runs ptc_bash_server.py (Python wrapper that spawns
                bash with one auto-generated function per tool).

        Returns:
            ProgrammaticExecResponse with status and optional tool_calls
        """
        if lang not in ("py", "bash"):
            return ProgrammaticExecResponse(
                status="error",
                session_id=session_id,
                error=f"Unsupported PTC lang: {lang!r}",
            )

        execution_timeout = timeout or settings.max_execution_time
        execution_deadline = time.monotonic() + execution_timeout

        # Bash PTC sandbox runs as the bash uid; python PTC sandbox runs as py.
        sandbox_language = lang
        sandbox_info = self._sandbox_manager.create_sandbox(
            session_id=session_id,
            language=sandbox_language,
            repl_mode=False,
        )

        try:
            ptc_server_filename = (
                "ptc_bash_server.py" if lang == "bash" else "ptc_server.py"
            )
            ptc_server_path = Path("/opt") / ptc_server_filename
            if not ptc_server_path.exists():
                # Fallback: try relative path (local development)
                ptc_server_path = (
                    Path(__file__).parent.parent.parent / "docker" / ptc_server_filename
                )

            if ptc_server_path.exists():
                self._sandbox_manager.copy_content_to_sandbox(
                    sandbox_info,
                    ptc_server_path.read_bytes(),
                    f"/mnt/data/{ptc_server_filename}",
                    language=sandbox_language,
                )
            else:
                self._sandbox_manager.destroy_sandbox(sandbox_info)
                return ProgrammaticExecResponse(
                    status="error",
                    session_id=session_id,
                    error=f"PTC server script not found: {ptc_server_filename}",
                )

            # Mount any provided files
            if files:
                file_error = await self._mount_requested_files(
                    sandbox_info=sandbox_info,
                    files=files,
                    language=sandbox_language,
                )
                if file_error:
                    self._sandbox_manager.destroy_sandbox(sandbox_info)
                    return ProgrammaticExecResponse(
                        status="error",
                        session_id=session_id,
                        error=file_error,
                    )

            # Both server scripts are launched via python3 — the bash variant
            # is itself a Python wrapper that spawns bash internally.
            env = self._sandbox_manager.executor._build_sanitized_env(sandbox_language)
            shell_command = [
                "/bin/sh",
                "-c",
                f"python3 /mnt/data/{ptc_server_filename}",
            ]
            nsjail_args = self._nsjail_config.build_args(
                sandbox_dir=str(sandbox_info.data_dir),
                command=shell_command,
                language=sandbox_language,
                timeout=execution_timeout,
                # Honor ENABLE_SANDBOX_NETWORK so PTC sandboxes can also
                # reach the inline egress proxy for skill installs.
                network=bool(settings.enable_sandbox_network),
                env=env,
            )

            # Build wrapper command (same pattern as SandboxExecutor)
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
                f"mount --bind /tmp/empty_proc /proc && "
                f"{nsjail_cmd}"
            )

            # Start subprocess
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
                start_new_session=True,
            )

            # Send initial request with code and tools
            tools_payload = [
                {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                }
                for t in tools
            ]
            initial_request = json.dumps({"code": code, "tools": tools_payload})
            initial_request += PTC_DELIMITER

            assert proc.stdin is not None
            proc.stdin.write(initial_request.encode("utf-8"))
            await proc.stdin.drain()

            # Read response from ptc_server
            return await self._read_ptc_response(
                proc=proc,
                sandbox_info=sandbox_info,
                session_id=session_id,
                timeout=execution_timeout,
                execution_deadline=execution_deadline,
                execution_timeout_seconds=execution_timeout,
                lang=lang,
            )

        except Exception as e:
            # Cleanup sandbox on error
            self._sandbox_manager.destroy_sandbox(sandbox_info)
            logger.error(
                "PTC execution failed",
                session_id=session_id[:12],
                error=str(e),
            )
            return ProgrammaticExecResponse(
                status="error",
                session_id=session_id,
                error=f"Execution failed: {str(e)}",
            )

    async def continue_execution(
        self,
        continuation_token: str,
        tool_results: List[PTCToolResult],
    ) -> ProgrammaticExecResponse:
        """Continue a paused PTC execution with tool results.

        Args:
            continuation_token: Token from a previous tool_call_required response
            tool_results: Results for the requested tool calls

        Returns:
            ProgrammaticExecResponse with updated status
        """
        ctx = self._paused_contexts.get(continuation_token)
        if not ctx:
            return ProgrammaticExecResponse(
                status="error",
                error="Invalid or expired continuation token",
            )

        # Cancel the timeout
        if ctx.timeout_handle:
            ctx.timeout_handle.cancel()
            ctx.timeout_handle = None

        # Check round trip limit
        ctx.round_trip_count += 1
        if ctx.round_trip_count > PTC_MAX_ROUND_TRIPS:
            await self._cleanup_paused_context(continuation_token)
            return ProgrammaticExecResponse(
                status="error",
                session_id=ctx.session_id,
                error=f"Maximum round trips ({PTC_MAX_ROUND_TRIPS}) exceeded",
            )

        remaining_timeout = max(
            0,
            math.ceil(ctx.execution_deadline - time.monotonic()),
        )
        if remaining_timeout <= 0:
            await self._cleanup_paused_context(continuation_token)
            return ProgrammaticExecResponse(
                status="error",
                session_id=ctx.session_id,
                error=(
                    "Execution timed out after "
                    f"{ctx.execution_timeout_seconds} seconds"
                ),
                stdout=ctx.accumulated_stdout,
                stderr=ctx.accumulated_stderr,
            )

        try:
            # Send tool results to the subprocess
            results_payload = {
                "type": "tool_results",
                "results": [
                    {
                        "call_id": r.call_id,
                        "result": r.result,
                        "is_error": r.is_error,
                        "error_message": r.error_message,
                    }
                    for r in tool_results
                ],
            }
            data = json.dumps(results_payload) + PTC_DELIMITER
            assert ctx.process.stdin is not None
            ctx.process.stdin.write(data.encode("utf-8"))
            await ctx.process.stdin.drain()

            # Remove from paused (will be re-added if another tool_call happens)
            del self._paused_contexts[continuation_token]

            # Read next response
            return await self._read_ptc_response(
                proc=ctx.process,
                sandbox_info=ctx.sandbox_info,
                session_id=ctx.session_id,
                timeout=max(1, remaining_timeout),
                execution_deadline=ctx.execution_deadline,
                execution_timeout_seconds=ctx.execution_timeout_seconds,
                accumulated_stdout=ctx.accumulated_stdout,
                accumulated_stderr=ctx.accumulated_stderr,
                round_trip_count=ctx.round_trip_count,
                lang=ctx.lang,
            )

        except Exception as e:
            await self._cleanup_paused_context(continuation_token)
            logger.error(
                "PTC continuation failed",
                continuation_token=continuation_token[:12],
                error=str(e),
            )
            return ProgrammaticExecResponse(
                status="error",
                session_id=ctx.session_id,
                error=f"Continuation failed: {str(e)}",
            )

    async def _read_ptc_response(
        self,
        proc: asyncio.subprocess.Process,
        sandbox_info: SandboxInfo,
        session_id: str,
        timeout: int,
        execution_deadline: float,
        execution_timeout_seconds: int,
        accumulated_stdout: str = "",
        accumulated_stderr: str = "",
        round_trip_count: int = 0,
        lang: str = "py",
    ) -> ProgrammaticExecResponse:
        """Read and process a response from the PTC server subprocess.

        Args:
            proc: The subprocess running ptc_server.py
            sandbox_info: Sandbox info for cleanup
            session_id: Session identifier
            timeout: Timeout in seconds
            execution_deadline: Monotonic deadline for total execution
            execution_timeout_seconds: Total allowed execution time
            accumulated_stdout: Previously accumulated stdout
            accumulated_stderr: Previously accumulated stderr
            round_trip_count: Current round trip count

        Returns:
            ProgrammaticExecResponse
        """
        try:
            # Read stdout until we get a complete PTC message
            stdout_buf = ""
            stderr_buf = ""

            async def read_until_delimiter() -> None:
                nonlocal stdout_buf
                assert proc.stdout is not None
                while PTC_DELIMITER not in stdout_buf:
                    chunk = await proc.stdout.read(4096)
                    if not chunk:
                        break
                    stdout_buf += chunk.decode("utf-8", errors="replace")

            try:
                await asyncio.wait_for(
                    read_until_delimiter(),
                    timeout=timeout + 5,
                )
            except asyncio.TimeoutError:
                self._kill_process(proc)
                self._sandbox_manager.destroy_sandbox(sandbox_info)
                return ProgrammaticExecResponse(
                    status="error",
                    session_id=session_id,
                    error=(
                        "Execution timed out after "
                        f"{execution_timeout_seconds} seconds"
                    ),
                    stdout=accumulated_stdout,
                    stderr=accumulated_stderr,
                )

            # Also read any stderr
            try:
                assert proc.stderr is not None
                stderr_data = await asyncio.wait_for(
                    proc.stderr.read(65536),
                    timeout=0.5,
                )
                if stderr_data:
                    stderr_buf = stderr_data.decode("utf-8", errors="replace")
            except asyncio.TimeoutError:
                pass

            # Parse response
            if PTC_DELIMITER not in stdout_buf:
                # Process may have exited without sending delimiter
                self._kill_process(proc)
                self._sandbox_manager.destroy_sandbox(sandbox_info)
                if time.monotonic() >= execution_deadline:
                    return ProgrammaticExecResponse(
                        status="error",
                        session_id=session_id,
                        error=(
                            "Execution timed out after "
                            f"{execution_timeout_seconds} seconds"
                        ),
                        stdout=accumulated_stdout + stdout_buf,
                        stderr=accumulated_stderr + stderr_buf,
                    )
                return ProgrammaticExecResponse(
                    status="error",
                    session_id=session_id,
                    error="PTC server exited without response",
                    stdout=accumulated_stdout + stdout_buf,
                    stderr=accumulated_stderr + stderr_buf,
                )

            json_part = stdout_buf.split(PTC_DELIMITER)[0]

            # Sanitize control characters before parsing
            json_part = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", json_part)

            try:
                response = json.loads(json_part)
            except json.JSONDecodeError as e:
                self._kill_process(proc)
                self._sandbox_manager.destroy_sandbox(sandbox_info)
                return ProgrammaticExecResponse(
                    status="error",
                    session_id=session_id,
                    error=f"Invalid response from PTC server: {e}",
                    stdout=accumulated_stdout,
                    stderr=accumulated_stderr + stderr_buf,
                )

            msg_type = response.get("type", "")
            msg_stdout = response.get("stdout", "")
            msg_stderr = response.get("stderr", "")
            total_stdout = accumulated_stdout + msg_stdout
            total_stderr = accumulated_stderr + msg_stderr + stderr_buf

            if msg_type == "tool_calls":
                # Code is paused waiting for tool results
                calls = [
                    PTCToolCall(
                        id=c["id"],
                        name=c["name"],
                        input=c.get("input", {}),
                    )
                    for c in response.get("calls", [])
                ]

                # Generate continuation token and store context
                token = uuid.uuid4().hex
                loop = asyncio.get_event_loop()

                ctx = PausedContext(
                    sandbox_info=sandbox_info,
                    process=proc,
                    session_id=session_id,
                    lang=lang,
                    round_trip_count=round_trip_count,
                    accumulated_stdout=total_stdout,
                    accumulated_stderr=total_stderr,
                    execution_deadline=execution_deadline,
                    execution_timeout_seconds=execution_timeout_seconds,
                )

                # Set timeout for cleanup
                def _make_cleanup_callback(
                    tok: str,
                ) -> Callable[[], None]:
                    def _cb() -> None:
                        asyncio.ensure_future(self._cleanup_paused_context(tok))

                    return _cb

                ctx.timeout_handle = loop.call_later(
                    PTC_PAUSE_TIMEOUT,
                    _make_cleanup_callback(token),
                )

                self._paused_contexts[token] = ctx

                return ProgrammaticExecResponse(
                    status="tool_call_required",
                    session_id=session_id,
                    continuation_token=token,
                    tool_calls=calls,
                    stdout=total_stdout,
                    stderr=total_stderr,
                )

            elif msg_type == "completed":
                # Execution completed successfully
                self._kill_process(proc)
                self._sandbox_manager.destroy_sandbox(sandbox_info)
                return ProgrammaticExecResponse(
                    status="completed",
                    session_id=session_id,
                    stdout=total_stdout,
                    stderr=total_stderr,
                )

            elif msg_type == "error":
                # Execution failed
                self._kill_process(proc)
                self._sandbox_manager.destroy_sandbox(sandbox_info)
                return ProgrammaticExecResponse(
                    status="error",
                    session_id=session_id,
                    error=response.get("error", "Unknown error"),
                    stdout=total_stdout,
                    stderr=total_stderr,
                )

            else:
                # Unknown message type
                self._kill_process(proc)
                self._sandbox_manager.destroy_sandbox(sandbox_info)
                return ProgrammaticExecResponse(
                    status="error",
                    session_id=session_id,
                    error=f"Unknown PTC message type: {msg_type}",
                    stdout=total_stdout,
                    stderr=total_stderr,
                )

        except Exception as e:
            self._kill_process(proc)
            self._sandbox_manager.destroy_sandbox(sandbox_info)
            logger.error(
                "PTC response reading failed",
                session_id=session_id[:12],
                error=str(e),
            )
            return ProgrammaticExecResponse(
                status="error",
                session_id=session_id,
                error=f"Failed to read PTC response: {str(e)}",
                stdout=accumulated_stdout,
                stderr=accumulated_stderr,
            )

    async def _mount_requested_files(
        self,
        sandbox_info: SandboxInfo,
        files: List[PTCFileInput],
        language: str = "py",
    ) -> Optional[str]:
        """Mount referenced prior-session files into the sandbox."""
        for file_info in files:
            error = await self._mount_referenced_file(
                sandbox_info, file_info, language=language
            )
            if error:
                return error

        return None

    async def _mount_referenced_file(
        self,
        sandbox_info: SandboxInfo,
        file_info: PTCFileInput,
        language: str = "py",
    ) -> Optional[str]:
        """Resolve a stored file reference and mount it into /mnt/data."""
        if self._file_service is None:
            return "PTC file references are not available: file service not configured"

        stored_file = await self._file_service.get_file_info(
            file_info.session_id,
            file_info.id,
        )
        if stored_file is None:
            return (
                "Referenced PTC file metadata could not be loaded: "
                f"{file_info.session_id}/{file_info.id}"
            )

        content = await self._file_service.get_file_content(
            file_info.session_id,
            file_info.id,
        )
        if content is None:
            return (
                "Referenced PTC file could not be loaded: "
                f"{file_info.session_id}/{file_info.id}"
            )

        filename = self._normalize_mount_filename(
            file_info.name or stored_file.filename
        )
        self._sandbox_manager.copy_content_to_sandbox(
            sandbox_info,
            content,
            f"/mnt/data/{filename}",
            language=language,
        )
        return None

    def _normalize_mount_filename(self, filename: Optional[str]) -> str:
        """Sanitize filename for /mnt/data while preserving subdirectories.

        Aligned with Item 4b's sanitize_relative_path so PTC file mounts use
        the same rules as the main /exec mount path. Filenames may legitimately
        contain `/` (skill bundles, nested data); only `..` traversal is rejected.
        """
        from .execution.output import OutputProcessor

        candidate = (filename or "").strip()
        normalized = OutputProcessor.sanitize_relative_path(candidate)
        if not normalized or normalized == "_":
            raise ValueError("Referenced PTC file input must include a valid name")
        return normalized

    def _kill_process(self, proc: asyncio.subprocess.Process) -> None:
        """Kill a subprocess and its process group."""
        if proc.returncode is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    async def _cleanup_paused_context(self, token: str) -> None:
        """Clean up a paused PTC context (on timeout or error)."""
        ctx = self._paused_contexts.pop(token, None)
        if ctx is None:
            return

        if ctx.timeout_handle:
            ctx.timeout_handle.cancel()

        self._kill_process(ctx.process)
        try:
            await ctx.process.wait()
        except Exception:
            pass

        self._sandbox_manager.destroy_sandbox(ctx.sandbox_info)
        logger.debug(
            "Cleaned up paused PTC context",
            token=token[:12],
            session_id=ctx.session_id[:12],
        )

    async def cleanup_all(self) -> None:
        """Clean up all paused PTC contexts. Called during shutdown."""
        tokens = list(self._paused_contexts.keys())
        for token in tokens:
            await self._cleanup_paused_context(token)
        logger.info("Cleaned up all PTC contexts", count=len(tokens))
