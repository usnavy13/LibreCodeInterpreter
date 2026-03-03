"""Programmatic Tool Calling (PTC) service.

Manages sandbox lifecycle for PTC executions where code can pause
to request external tool calls and resume with results.
"""

import asyncio
import json
import os
import re
import shlex
import signal
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import structlog

from ..config import settings
from ..models.programmatic import (
    PTCToolCall,
    PTCToolDefinition,
    PTCToolResult,
    ProgrammaticExecResponse,
)
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
    round_trip_count: int = 0
    timeout_handle: Optional[asyncio.TimerHandle] = None
    accumulated_stdout: str = ""
    accumulated_stderr: str = ""


class ProgrammaticService:
    """Manages PTC execution lifecycle.

    Creates nsjail sandboxes, runs ptc_server.py inside them, and
    manages the request/response protocol for tool calls.
    """

    def __init__(self, sandbox_manager: Optional[SandboxManager] = None):
        self._sandbox_manager = sandbox_manager or SandboxManager()
        self._nsjail_config = NsjailConfig()
        self._paused_contexts: Dict[str, PausedContext] = {}

    async def start_execution(
        self,
        code: str,
        tools: List[PTCToolDefinition],
        session_id: str,
        timeout: Optional[int] = None,
        files: Optional[List[Dict[str, Any]]] = None,
    ) -> ProgrammaticExecResponse:
        """Start a new PTC execution.

        Creates an nsjail sandbox, copies ptc_server.py into it,
        and starts execution with the provided code and tools.

        Args:
            code: Python code to execute
            tools: Tool definitions available to the code
            session_id: Session identifier
            timeout: Execution timeout in seconds
            files: Optional files to mount in sandbox

        Returns:
            ProgrammaticExecResponse with status and optional tool_calls
        """
        execution_timeout = timeout or settings.max_execution_time

        # Create sandbox
        sandbox_info = self._sandbox_manager.create_sandbox(
            session_id=session_id,
            language="py",
            repl_mode=False,
        )

        try:
            # Copy ptc_server.py into the sandbox data dir
            ptc_server_path = Path("/opt/ptc_server.py")
            if not ptc_server_path.exists():
                # Fallback: try relative path (local development)
                ptc_server_path = (
                    Path(__file__).parent.parent.parent / "docker" / "ptc_server.py"
                )

            if ptc_server_path.exists():
                self._sandbox_manager.copy_content_to_sandbox(
                    sandbox_info,
                    ptc_server_path.read_bytes(),
                    "/mnt/data/ptc_server.py",
                    language="py",
                )
            else:
                return ProgrammaticExecResponse(
                    status="error",
                    session_id=session_id,
                    error="PTC server script not found",
                )

            # Mount any provided files
            if files:
                for file_info in files:
                    filename = file_info.get("filename", "")
                    content = file_info.get("content", b"")
                    if filename and content:
                        self._sandbox_manager.copy_content_to_sandbox(
                            sandbox_info,
                            content if isinstance(content, bytes) else content.encode(),
                            f"/mnt/data/{filename}",
                            language="py",
                        )

            # Build nsjail command - wrap in /bin/sh -c like SandboxExecutor
            env = self._sandbox_manager.executor._build_sanitized_env("py")
            shell_command = [
                "/bin/sh",
                "-c",
                "python3 /mnt/data/ptc_server.py",
            ]
            nsjail_args = self._nsjail_config.build_args(
                sandbox_dir=str(sandbox_info.data_dir),
                command=shell_command,
                language="py",
                timeout=execution_timeout,
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
                timeout=settings.max_execution_time,
                accumulated_stdout=ctx.accumulated_stdout,
                accumulated_stderr=ctx.accumulated_stderr,
                round_trip_count=ctx.round_trip_count,
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
        accumulated_stdout: str = "",
        accumulated_stderr: str = "",
        round_trip_count: int = 0,
    ) -> ProgrammaticExecResponse:
        """Read and process a response from the PTC server subprocess.

        Args:
            proc: The subprocess running ptc_server.py
            sandbox_info: Sandbox info for cleanup
            session_id: Session identifier
            timeout: Timeout in seconds
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
                    error=f"Execution timed out after {timeout} seconds",
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
                    round_trip_count=round_trip_count,
                    accumulated_stdout=total_stdout,
                    accumulated_stderr=total_stderr,
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
