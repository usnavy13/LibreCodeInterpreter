"""
Code execution runner for the executor service.

Coordinates code execution across all supported languages,
using the appropriate execution method for each.
"""

import asyncio
import logging
import os
import tempfile
import time
from typing import Any, Dict, List, Optional

from .languages import get_language, get_supported_languages, is_supported_language
from .sandbox import run_sandboxed, run_with_file, run_with_stdin
from .python_state import execute_python_code
from .models import ExecuteRequest, ExecuteResponse, FileReference

logger = logging.getLogger(__name__)


async def execute_code(
    request: ExecuteRequest,
    working_dir: str = "/mnt/data",
) -> ExecuteResponse:
    """
    Execute code in the specified language.

    Args:
        request: Execution request with code, language, timeout, etc.
        working_dir: Working directory for execution

    Returns:
        ExecuteResponse with results
    """
    start_time = time.perf_counter()

    # Validate language
    if not is_supported_language(request.language):
        return ExecuteResponse(
            exit_code=1,
            stdout="",
            stderr=f"Unsupported language: {request.language}. Supported: {', '.join(get_supported_languages())}",
            execution_time_ms=0,
            error=f"Unsupported language: {request.language}",
        )

    language_config = get_language(request.language)

    # Ensure working directory exists
    os.makedirs(working_dir, exist_ok=True)

    # Write any provided files to /mnt/data directly (not subdirectory)
    # This ensures files are accessible at /mnt/data/filename.csv as users expect
    if request.files:
        parent_dir = os.path.dirname(working_dir)  # /mnt/data
        for file_ref in request.files:
            await write_file_to_working_dir(file_ref, parent_dir)

    try:
        # Python has special handling for state persistence
        if request.language == "py":
            result = await execute_python(
                code=request.code,
                timeout=request.timeout,
                working_dir=working_dir,
                initial_state=request.initial_state,
                capture_state=request.capture_state,
            )
        elif language_config.uses_stdin:
            result = await execute_stdin_language(
                code=request.code,
                language_config=language_config,
                timeout=request.timeout,
                working_dir=working_dir,
            )
        else:
            result = await execute_file_language(
                code=request.code,
                language_config=language_config,
                timeout=request.timeout,
                working_dir=working_dir,
            )

        # Detect generated files in execution subdirectory
        generated_files = await detect_generated_files(working_dir)

        # Also scan parent directory for files created with absolute paths (e.g., /mnt/data/chart.png)
        parent_dir = os.path.dirname(working_dir)
        if parent_dir and parent_dir != working_dir and os.path.isdir(parent_dir):
            try:
                parent_files = await detect_generated_files(parent_dir, include_content=True)
                exec_dir_name = os.path.basename(working_dir)
                for f in parent_files:
                    # Only include files not in any execution subdirectory
                    if f"exec_" not in f.get("path", ""):
                        generated_files.append(f)
                        logger.info(f"Found file in parent directory: {f.get('filename')}")
            except Exception as e:
                logger.warning(f"Failed to scan parent directory: {e}")

        execution_time_ms = (time.perf_counter() - start_time) * 1000

        return ExecuteResponse(
            exit_code=result.get("exit_code", 0),
            stdout=result.get("stdout", ""),
            stderr=result.get("stderr", ""),
            execution_time_ms=execution_time_ms,
            state=result.get("state"),
            state_errors=result.get("state_errors", []),
            generated_files=generated_files,
            timed_out=result.get("timed_out", False),
        )

    except Exception as e:
        logger.exception(f"Execution failed: {e}")
        execution_time_ms = (time.perf_counter() - start_time) * 1000

        return ExecuteResponse(
            exit_code=1,
            stdout="",
            stderr=str(e),
            execution_time_ms=execution_time_ms,
            error=str(e),
        )


async def execute_python(
    code: str,
    timeout: int,
    working_dir: str,
    initial_state: Optional[str] = None,
    capture_state: bool = True,
) -> Dict[str, Any]:
    """
    Execute Python code with state handling.

    For Python, we use in-process execution with state serialization
    rather than subprocess, for better state capture.
    """
    # Run in thread pool to avoid blocking the event loop
    loop = asyncio.get_event_loop()

    result = await loop.run_in_executor(
        None,
        lambda: execute_python_code(
            code=code,
            timeout=timeout,
            working_dir=working_dir,
            initial_state=initial_state,
            capture_state=capture_state,
        )
    )

    return result


async def execute_stdin_language(
    code: str,
    language_config,
    timeout: int,
    working_dir: str,
) -> Dict[str, Any]:
    """
    Execute code for languages that accept stdin input.

    Languages: py (without state), js, php, r
    """
    exit_code, stdout, stderr, timed_out = await run_with_stdin(
        code=code,
        language_config=language_config,
        timeout=timeout,
        working_dir=working_dir,
    )

    return {
        "exit_code": exit_code,
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
        "timed_out": timed_out,
    }


async def execute_file_language(
    code: str,
    language_config,
    timeout: int,
    working_dir: str,
) -> Dict[str, Any]:
    """
    Execute code for languages that require file-based execution.

    Languages: ts, go, java, c, cpp, rs, f90, d
    """
    exit_code, stdout, stderr, timed_out = await run_with_file(
        code=code,
        language_config=language_config,
        timeout=timeout,
        working_dir=working_dir,
    )

    return {
        "exit_code": exit_code,
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
        "timed_out": timed_out,
    }


async def write_file_to_working_dir(
    file_ref: FileReference,
    working_dir: str,
) -> str:
    """
    Write a file reference to the working directory.

    Args:
        file_ref: File reference with content or path
        working_dir: Target working directory

    Returns:
        Path to the written file
    """
    filename = file_ref.filename
    filepath = os.path.join(working_dir, filename)

    # Create parent directories if needed
    os.makedirs(os.path.dirname(filepath) if os.path.dirname(filepath) else working_dir, exist_ok=True)

    # Try to get content (handles both raw bytes and base64-encoded)
    content = file_ref.get_content()
    if content:
        # Write content directly
        with open(filepath, "wb") as f:
            f.write(content)
        logger.info(f"Wrote file {filename} ({len(content)} bytes) to {filepath}")
    elif file_ref.path and os.path.exists(file_ref.path):
        # Copy from path
        import shutil
        shutil.copy2(file_ref.path, filepath)
        logger.info(f"Copied file from {file_ref.path} to {filepath}")
    else:
        logger.warning(f"No content available for file {filename}")

    return filepath


async def detect_generated_files(
    working_dir: str,
    excluded_extensions: Optional[set] = None,
    include_content: bool = True,
    max_content_size: int = 10 * 1024 * 1024,  # 10MB limit
) -> List[Dict[str, Any]]:
    """
    Detect files generated during execution.

    Args:
        working_dir: Working directory to scan
        excluded_extensions: File extensions to exclude
        include_content: Whether to include base64-encoded file content
        max_content_size: Maximum file size to include content for

    Returns:
        List of file info dicts with optional content_b64 field
    """
    import base64

    if excluded_extensions is None:
        excluded_extensions = {".pyc", ".pyo", "__pycache__"}

    generated = []

    try:
        for entry in os.scandir(working_dir):
            if entry.is_file():
                ext = os.path.splitext(entry.name)[1]
                if ext not in excluded_extensions and not entry.name.startswith('.'):
                    stat = entry.stat()
                    file_info = {
                        "filename": entry.name,
                        "path": entry.path,
                        "size": stat.st_size,
                        "modified": stat.st_mtime,
                    }

                    # Include base64-encoded content for Azure mode
                    if include_content and stat.st_size <= max_content_size:
                        try:
                            with open(entry.path, 'rb') as f:
                                content = f.read()
                            file_info["content_b64"] = base64.b64encode(content).decode('ascii')
                            logger.debug(f"Included content for {entry.name} ({stat.st_size} bytes)")
                        except Exception as e:
                            logger.warning(f"Failed to read content for {entry.name}: {e}")
                            file_info["content_error"] = str(e)

                    generated.append(file_info)
    except Exception as e:
        logger.warning(f"Failed to scan working directory: {e}")

    return generated
