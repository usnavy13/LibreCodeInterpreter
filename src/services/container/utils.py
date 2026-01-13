"""Shared utilities for container operations.

This module contains common patterns extracted from container services
to reduce code duplication.
"""

import asyncio
from typing import List, Optional

import structlog
from docker.models.containers import Container

logger = structlog.get_logger(__name__)


async def wait_for_container_ready(
    container: Container,
    max_wait: float = 2.0,
    interval: float = 0.05,
    stable_checks_required: int = 3,
) -> bool:
    """
    Wait for a container to reach a stable running state.

    Uses polling with stability checks to ensure the container
    is truly running before returning.

    Args:
        container: Docker container to wait for
        max_wait: Maximum time to wait in seconds
        interval: Polling interval in seconds
        stable_checks_required: Number of consecutive running checks required

    Returns:
        True if container is running, False otherwise
    """
    stable_checks = 0
    total_wait = 0.0

    while total_wait < max_wait:
        try:
            container.reload()
            if getattr(container, "status", "") == "running":
                stable_checks += 1
                if stable_checks >= stable_checks_required:
                    return True
            else:
                stable_checks = 0
        except Exception:
            stable_checks = 0
        await asyncio.sleep(interval)
        total_wait += interval

    # Final check
    try:
        container.reload()
        return getattr(container, "status", "") == "running"
    except Exception:
        return False


def receive_socket_output(
    sock,
    chunk_size: int = 4096,
    timeout_exceptions: tuple = (TimeoutError, OSError),
) -> bytes:
    """
    Receive all output from a socket until closed or timeout.

    Args:
        sock: Raw socket to receive from
        chunk_size: Size of chunks to receive
        timeout_exceptions: Exception types that indicate timeout

    Returns:
        All received bytes concatenated
    """
    output_chunks: List[bytes] = []
    while True:
        try:
            chunk = sock.recv(chunk_size)
            if not chunk:
                break
            output_chunks.append(chunk)
        except timeout_exceptions:
            break
    return b"".join(output_chunks)


async def run_in_executor(func, *args):
    """
    Run a blocking function in the default thread pool executor.

    Args:
        func: Blocking function to run
        *args: Arguments to pass to the function

    Returns:
        Result of the function
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, func, *args)
