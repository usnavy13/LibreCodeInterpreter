"""REPL-based code execution for pre-warmed Python sandboxes.

This module provides fast code execution by communicating with a
running Python REPL inside an nsjail sandbox, eliminating interpreter startup.

The REPL server runs as the main process in the sandbox and communicates
via stdin/stdout using a JSON-based protocol with delimiters. This is
identical to the Docker version but uses subprocess pipes instead of
Docker attach sockets.
"""

import asyncio
import json
import time
import structlog
from dataclasses import dataclass, field
from datetime import datetime
from typing import Tuple, Optional, Dict, Any, List

from ...config import settings
from .nsjail import SandboxInfo

logger = structlog.get_logger(__name__)

# Protocol delimiter (must match repl_server.py)
DELIMITER = b"\n---END---\n"


@dataclass
class SandboxREPLProcess:
    """Represents a running REPL process inside an nsjail sandbox.

    This replaces the Docker Container as the handle for REPL communication.
    """

    process: asyncio.subprocess.Process
    sandbox_info: SandboxInfo
    created_at: datetime = field(default_factory=datetime.utcnow)
    ready: bool = False


class SandboxREPLExecutor:
    """Executes code via running REPL in an nsjail sandbox.

    Uses subprocess stdin/stdout pipes to communicate with the REPL server.
    This is much simpler than the Docker version since subprocess pipes
    give clean stdout without Docker stream headers.
    """

    def __init__(self):
        """Initialize REPL executor."""
        pass

    async def execute(
        self,
        process: SandboxREPLProcess,
        code: str,
        timeout: int = None,
        working_dir: str = "/mnt/data",
        args: Optional[List[str]] = None,
    ) -> Tuple[int, str, str]:
        """Execute code in running REPL.

        Args:
            process: REPL process to communicate with
            code: Python code to execute
            timeout: Maximum execution time in seconds
            working_dir: Working directory for code execution
            args: Optional list of command line arguments

        Returns:
            Tuple of (exit_code, stdout, stderr)
        """
        if timeout is None:
            timeout = settings.max_execution_time

        start_time = time.perf_counter()

        # Build request
        request = {"code": code, "timeout": timeout, "working_dir": working_dir}
        if args:
            request["args"] = args
        request_json = json.dumps(request)
        request_bytes = request_json.encode("utf-8") + DELIMITER

        try:
            response = await self._send_and_receive(
                process, request_bytes, timeout + 5
            )

            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.debug(
                "REPL execution completed",
                sandbox_id=process.sandbox_info.sandbox_id[:12],
                elapsed_ms=f"{elapsed_ms:.1f}",
                exit_code=response.get("exit_code", -1),
            )

            return self._parse_response(response)

        except asyncio.TimeoutError:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.warning(
                "REPL execution timed out",
                sandbox_id=process.sandbox_info.sandbox_id[:12],
                timeout=timeout,
                elapsed_ms=f"{elapsed_ms:.1f}",
            )
            return 124, "", f"Execution timed out after {timeout} seconds"

        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.error(
                "REPL execution failed",
                sandbox_id=process.sandbox_info.sandbox_id[:12],
                error=str(e),
                elapsed_ms=f"{elapsed_ms:.1f}",
            )
            return 1, "", f"REPL execution error: {str(e)}"

    async def execute_with_state(
        self,
        process: SandboxREPLProcess,
        code: str,
        timeout: int = None,
        working_dir: str = "/mnt/data",
        initial_state: Optional[str] = None,
        capture_state: bool = False,
        args: Optional[List[str]] = None,
    ) -> Tuple[int, str, str, Optional[str], List[str]]:
        """Execute code in running REPL with optional state persistence.

        Args:
            process: REPL process to communicate with
            code: Python code to execute
            timeout: Maximum execution time in seconds
            working_dir: Working directory for code execution
            initial_state: Base64-encoded state to restore before execution
            capture_state: Whether to capture state after execution
            args: Optional list of command line arguments

        Returns:
            Tuple of (exit_code, stdout, stderr, new_state, state_errors)
            new_state is base64-encoded cloudpickle, or None if not captured
        """
        if timeout is None:
            timeout = settings.max_execution_time

        start_time = time.perf_counter()

        # Build request with state options
        request = {"code": code, "timeout": timeout, "working_dir": working_dir}

        if initial_state:
            request["initial_state"] = initial_state

        if capture_state:
            request["capture_state"] = True

        if args:
            request["args"] = args

        request_json = json.dumps(request)
        request_bytes = request_json.encode("utf-8") + DELIMITER

        try:
            response = await self._send_and_receive(
                process, request_bytes, timeout + 10
            )

            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.debug(
                "REPL execution with state completed",
                sandbox_id=process.sandbox_info.sandbox_id[:12],
                elapsed_ms=f"{elapsed_ms:.1f}",
                exit_code=response.get("exit_code", -1),
                has_state="state" in response,
            )

            return self._parse_response_with_state(response)

        except asyncio.TimeoutError:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.warning(
                "REPL execution timed out",
                sandbox_id=process.sandbox_info.sandbox_id[:12],
                timeout=timeout,
                elapsed_ms=f"{elapsed_ms:.1f}",
            )
            return 124, "", f"Execution timed out after {timeout} seconds", None, []

        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.error(
                "REPL execution failed",
                sandbox_id=process.sandbox_info.sandbox_id[:12],
                error=str(e),
                elapsed_ms=f"{elapsed_ms:.1f}",
            )
            return 1, "", f"REPL execution error: {str(e)}", None, []

    async def _send_and_receive(
        self, process: SandboxREPLProcess, request: bytes, timeout: int
    ) -> Dict[str, Any]:
        """Send request to REPL and receive response via subprocess pipes.

        Unlike the Docker version, subprocess pipes give clean stdout
        without multiplexed stream headers, so no _strip_docker_headers
        is needed.

        Args:
            process: REPL process with stdin/stdout pipes
            request: Request bytes to send
            timeout: Timeout in seconds

        Returns:
            Parsed JSON response dict
        """
        proc = process.process

        if proc.returncode is not None:
            raise RuntimeError(
                f"REPL process has exited with code {proc.returncode}"
            )

        if proc.stdin is None or proc.stdout is None:
            raise RuntimeError("REPL process stdin/stdout not available")

        # Send request
        proc.stdin.write(request)
        await proc.stdin.drain()

        # Read response until delimiter
        response_bytes = b""

        async def _read_until_delimiter():
            nonlocal response_bytes
            while DELIMITER not in response_bytes:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                response_bytes += chunk

        await asyncio.wait_for(_read_until_delimiter(), timeout=timeout)

        # Parse response
        if DELIMITER in response_bytes:
            json_part = response_bytes.split(DELIMITER)[0]
            json_str = json_part.decode("utf-8", errors="replace")
            return json.loads(json_str)
        else:
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": "Invalid response from REPL: delimiter not found",
            }

    def _parse_response(self, response: Dict[str, Any]) -> Tuple[int, str, str]:
        """Parse REPL response into (exit_code, stdout, stderr).

        Args:
            response: JSON response from REPL

        Returns:
            Tuple of (exit_code, stdout, stderr)
        """
        return (
            response.get("exit_code", 1),
            response.get("stdout", ""),
            response.get("stderr", ""),
        )

    def _parse_response_with_state(
        self, response: Dict[str, Any]
    ) -> Tuple[int, str, str, Optional[str], List[str]]:
        """Parse REPL response including state data.

        Args:
            response: JSON response from REPL

        Returns:
            Tuple of (exit_code, stdout, stderr, state, state_errors)
        """
        return (
            response.get("exit_code", 1),
            response.get("stdout", ""),
            response.get("stderr", ""),
            response.get("state"),  # May be None
            response.get("state_errors", []),
        )

    async def check_health(
        self, process: SandboxREPLProcess, timeout: float = 5.0
    ) -> bool:
        """Check if REPL is responsive.

        Sends a simple health check code and verifies response.

        Args:
            process: REPL process to check
            timeout: Maximum time to wait for response

        Returns:
            True if REPL is healthy, False otherwise
        """
        try:
            exit_code, stdout, stderr = await self.execute(
                process, "print('health_check_ok')", timeout=int(timeout)
            )
            return exit_code == 0 and "health_check_ok" in stdout

        except Exception as e:
            logger.debug(
                "REPL health check failed",
                sandbox_id=process.sandbox_info.sandbox_id[:12],
                error=str(e),
            )
            return False

    async def wait_for_ready(
        self,
        process: SandboxREPLProcess,
        timeout: float = 10.0,
        poll_interval: float = 0.1,
    ) -> bool:
        """Wait for REPL to be ready by consuming its ready signal.

        The REPL server sends a ready signal (a JSON message with
        ``"status": "ready"``) on stdout after pre-loading libraries.
        This method reads that signal directly so it does not interfere
        with subsequent request/response pairs.

        Args:
            process: REPL process
            timeout: Maximum time to wait
            poll_interval: Time between checks

        Returns:
            True if REPL is ready, False if timeout
        """
        start_time = time.perf_counter()
        proc = process.process

        if proc.stdout is None:
            return False

        # Read the ready signal directly from stdout
        response_bytes = b""

        async def _read_ready_signal():
            nonlocal response_bytes
            while DELIMITER not in response_bytes:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                response_bytes += chunk

        try:
            await asyncio.wait_for(_read_ready_signal(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "REPL ready timeout waiting for ready signal",
                sandbox_id=process.sandbox_info.sandbox_id[:12],
                timeout=timeout,
            )
            return False

        if DELIMITER in response_bytes:
            json_part = response_bytes.split(DELIMITER)[0]
            try:
                ready_msg = json.loads(json_part.decode("utf-8", errors="replace"))
                if ready_msg.get("status") == "ready":
                    elapsed = time.perf_counter() - start_time
                    logger.info(
                        "REPL ready",
                        sandbox_id=process.sandbox_info.sandbox_id[:12],
                        elapsed_ms=f"{elapsed * 1000:.1f}",
                        preloaded=ready_msg.get("preloaded_modules"),
                    )
                    process.ready = True
                    return True
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.warning(
                    "REPL ready signal parse error",
                    sandbox_id=process.sandbox_info.sandbox_id[:12],
                    error=str(e),
                )

        logger.warning(
            "REPL ready timeout",
            sandbox_id=process.sandbox_info.sandbox_id[:12],
            timeout=timeout,
        )
        return False
