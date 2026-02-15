"""REPL-based code execution for pre-warmed Python containers.

This module provides fast code execution by communicating with a
running Python REPL inside the container, eliminating interpreter startup.

The REPL server runs as PID 1 in the container and communicates via
stdin/stdout using a JSON-based protocol with delimiters.
"""

import asyncio
import json
import time
import structlog
from typing import Tuple, Optional, Dict, Any, List
from docker.models.containers import Container

from ...config import settings

logger = structlog.get_logger(__name__)

# Protocol delimiter (must match repl_server.py)
DELIMITER = b"\n---END---\n"


class REPLExecutor:
    """Executes code via running REPL in container.

    Uses Docker's attach socket to communicate with the REPL server
    that's running as PID 1 in the container.
    """

    def __init__(self, docker_client):
        """Initialize REPL executor.

        Args:
            docker_client: Docker client instance
        """
        self.client = docker_client

    async def execute(
        self,
        container: Container,
        code: str,
        timeout: int = None,
        working_dir: str = "/mnt/data",
        args: Optional[List[str]] = None,
    ) -> Tuple[int, str, str]:
        """Execute code in running REPL.

        Args:
            container: Docker container with REPL server running
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
            # Execute via Docker attach
            response = await self._send_and_receive(
                container, request_bytes, timeout + 5
            )

            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.debug(
                "REPL execution completed",
                container_id=container.id[:12],
                elapsed_ms=f"{elapsed_ms:.1f}",
                exit_code=response.get("exit_code", -1),
            )

            return self._parse_response(response)

        except asyncio.TimeoutError:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.warning(
                "REPL execution timed out",
                container_id=container.id[:12],
                timeout=timeout,
                elapsed_ms=f"{elapsed_ms:.1f}",
            )
            return 124, "", f"Execution timed out after {timeout} seconds"

        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.error(
                "REPL execution failed",
                container_id=container.id[:12],
                error=str(e),
                elapsed_ms=f"{elapsed_ms:.1f}",
            )
            return 1, "", f"REPL execution error: {str(e)}"

    async def execute_with_state(
        self,
        container: Container,
        code: str,
        timeout: int = None,
        working_dir: str = "/mnt/data",
        initial_state: Optional[str] = None,
        capture_state: bool = False,
        args: Optional[List[str]] = None,
    ) -> Tuple[int, str, str, Optional[str], List[str]]:
        """Execute code in running REPL with optional state persistence.

        Args:
            container: Docker container with REPL server running
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
            # Execute via Docker attach
            response = await self._send_and_receive(
                container, request_bytes, timeout + 10
            )

            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.debug(
                "REPL execution with state completed",
                container_id=container.id[:12],
                elapsed_ms=f"{elapsed_ms:.1f}",
                exit_code=response.get("exit_code", -1),
                has_state="state" in response,
            )

            return self._parse_response_with_state(response)

        except asyncio.TimeoutError:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.warning(
                "REPL execution timed out",
                container_id=container.id[:12],
                timeout=timeout,
                elapsed_ms=f"{elapsed_ms:.1f}",
            )
            return 124, "", f"Execution timed out after {timeout} seconds", None, []

        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.error(
                "REPL execution failed",
                container_id=container.id[:12],
                error=str(e),
                elapsed_ms=f"{elapsed_ms:.1f}",
            )
            return 1, "", f"REPL execution error: {str(e)}", None, []

    async def _send_and_receive(
        self, container: Container, request: bytes, timeout: int
    ) -> Dict[str, Any]:
        """Send request to REPL and receive response.

        Uses Docker attach socket for bidirectional communication
        with the REPL server running in the container.

        Args:
            container: Docker container
            request: Request bytes to send
            timeout: Timeout in seconds

        Returns:
            Parsed JSON response dict
        """
        loop = asyncio.get_event_loop()

        def _sync_communicate():
            """Synchronous communication with container (runs in executor)."""
            import time as sync_time

            t0 = sync_time.perf_counter()

            # Attach to container's stdin/stdout
            sock = self.client.api.attach_socket(
                container.id,
                params={"stdin": True, "stdout": True, "stderr": True, "stream": True},
            )
            t1 = sync_time.perf_counter()

            try:
                # Get the raw socket
                raw_sock = sock._sock
                raw_sock.settimeout(timeout)

                # Send request
                raw_sock.sendall(request)
                t2 = sync_time.perf_counter()

                # Read response until we get the delimiter
                response_bytes = b""
                while DELIMITER not in response_bytes:
                    try:
                        chunk = raw_sock.recv(4096)
                        if not chunk:
                            break
                        response_bytes += chunk
                    except Exception as e:
                        if "timed out" in str(e).lower():
                            raise asyncio.TimeoutError()
                        raise

                t3 = sync_time.perf_counter()

                # Log timing breakdown
                logger.debug(
                    "REPL socket timing",
                    attach_ms=f"{(t1-t0)*1000:.1f}",
                    send_ms=f"{(t2-t1)*1000:.1f}",
                    recv_ms=f"{(t3-t2)*1000:.1f}",
                    total_ms=f"{(t3-t0)*1000:.1f}",
                )

                # Parse response
                if DELIMITER in response_bytes:
                    json_part = response_bytes.split(DELIMITER)[0]

                    # Strip Docker stream headers (multiplexed format)
                    # Format: [type:1][0:3][size:4][payload]
                    json_part = self._strip_docker_headers(json_part)

                    # Decode with error handling for any remaining binary data
                    try:
                        json_str = json_part.decode("utf-8")
                    except UnicodeDecodeError:
                        # Try to find JSON in the data by looking for { and }
                        json_str = json_part.decode("utf-8", errors="replace")
                        # Extract the JSON object
                        start = json_str.find("{")
                        end = json_str.rfind("}")
                        if start >= 0 and end > start:
                            json_str = json_str[start : end + 1]

                    return json.loads(json_str)
                else:
                    return {
                        "exit_code": 1,
                        "stdout": "",
                        "stderr": f"Invalid response from REPL: delimiter not found",
                    }

            finally:
                try:
                    sock.close()
                except:
                    pass

        # Run sync communication in executor
        return await loop.run_in_executor(None, _sync_communicate)

    def _strip_docker_headers(self, data: bytes) -> bytes:
        """Strip Docker multiplexed stream headers from data.

        Docker attach socket uses multiplexed format where each chunk
        is prefixed with 8 bytes: [type:1][0:3][size:4]

        Args:
            data: Raw bytes from Docker socket

        Returns:
            Data with stream headers stripped
        """
        result = bytearray()
        pos = 0

        while pos < len(data):
            # Check for Docker stream header
            if pos + 8 <= len(data) and data[pos : pos + 1] in (
                b"\x01",
                b"\x02",
                b"\x00",
            ):
                # Validate padding bytes are zero (bytes 1-3)
                if data[pos + 1 : pos + 4] != b"\x00\x00\x00":
                    # Invalid header padding, skip to JSON search
                    if data[pos : pos + 1] == b"{":
                        result.extend(data[pos:])
                        break
                    pos += 1
                    continue

                # Read the payload size from bytes 4-7 (big-endian)
                size = int.from_bytes(data[pos + 4 : pos + 8], byteorder="big")
                payload_start = pos + 8

                if size > 0 and payload_start + size <= len(data):
                    # Complete frame: extract full payload
                    result.extend(data[payload_start : payload_start + size])
                    pos = payload_start + size
                    continue
                elif size > 0 and payload_start < len(data):
                    # Partial final frame: take available data
                    result.extend(data[payload_start:])
                    break
                else:
                    # Empty or invalid size, skip this header
                    pos = payload_start
                    continue

            # Not a header or invalid, try to find JSON start
            if data[pos : pos + 1] == b"{":
                result.extend(data[pos:])
                break
            pos += 1

        return bytes(result) if result else data

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

    async def check_health(self, container: Container, timeout: float = 5.0) -> bool:
        """Check if REPL is responsive.

        Sends a simple health check code and verifies response.

        Args:
            container: Docker container to check
            timeout: Maximum time to wait for response

        Returns:
            True if REPL is healthy, False otherwise
        """
        try:
            exit_code, stdout, stderr = await self.execute(
                container, "print('health_check_ok')", timeout=int(timeout)
            )
            return exit_code == 0 and "health_check_ok" in stdout

        except Exception as e:
            logger.debug(
                "REPL health check failed", container_id=container.id[:12], error=str(e)
            )
            return False

    async def wait_for_ready(
        self, container: Container, timeout: float = 10.0, poll_interval: float = 0.1
    ) -> bool:
        """Wait for REPL to be ready.

        The REPL server sends a ready signal when it has finished
        pre-loading libraries. This method waits for that signal
        or falls back to health check.

        Args:
            container: Docker container
            timeout: Maximum time to wait
            poll_interval: Time between checks

        Returns:
            True if REPL is ready, False if timeout
        """
        start_time = time.perf_counter()

        while (time.perf_counter() - start_time) < timeout:
            # Try health check
            if await self.check_health(container, timeout=2.0):
                elapsed = time.perf_counter() - start_time
                logger.info(
                    "REPL ready",
                    container_id=container.id[:12],
                    elapsed_ms=f"{elapsed * 1000:.1f}",
                )
                return True

            # Wait before next check
            await asyncio.sleep(poll_interval)

        logger.warning(
            "REPL ready timeout", container_id=container.id[:12], timeout=timeout
        )
        return False
