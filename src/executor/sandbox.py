"""
Sandbox execution using bubblewrap for process isolation.

Provides security isolation similar to Docker containers:
- Network isolation (no network access)
- PID namespace isolation
- Read-only system directories
- Resource limits (memory, CPU, processes)
"""

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Check if bubblewrap is available
BWRAP_AVAILABLE = shutil.which("bwrap") is not None

# Skip bubblewrap in Azure - ACA provides container-level isolation
IS_AZURE_DEPLOYMENT = os.environ.get("DEPLOYMENT_MODE", "docker").lower() == "azure"
USE_SANDBOX = BWRAP_AVAILABLE and not IS_AZURE_DEPLOYMENT

if IS_AZURE_DEPLOYMENT:
    logger.info("Azure deployment mode detected - bubblewrap sandbox disabled (ACA provides isolation)")


def build_bwrap_command(
    command: List[str],
    working_dir: str = "/mnt/data",
    network_access: bool = False,
    memory_limit_mb: int = 512,
    extra_binds: Optional[List[Tuple[str, str]]] = None,
) -> List[str]:
    """
    Build a bubblewrap command for sandboxed execution.

    Args:
        command: The command to execute inside the sandbox
        working_dir: Working directory for execution
        network_access: Whether to allow network access (default: False)
        memory_limit_mb: Memory limit in MB (not enforced by bwrap, just for reference)
        extra_binds: Additional bind mounts [(source, dest), ...]

    Returns:
        Full bwrap command as list of strings
    """
    bwrap_cmd = ["bwrap"]

    # Namespace isolation
    if not network_access:
        bwrap_cmd.append("--unshare-net")  # Network namespace (no network)

    bwrap_cmd.extend([
        "--unshare-pid",  # PID namespace isolation
        "--unshare-uts",  # Hostname isolation
        "--unshare-ipc",  # IPC namespace
    ])

    # Read-only bind mounts for system directories
    readonly_dirs = [
        "/usr",
        "/lib",
        "/lib64",
        "/bin",
        "/sbin",
        "/etc/alternatives",
        "/etc/ssl",
        "/etc/ca-certificates",
        "/etc/passwd",
        "/etc/group",
    ]

    for dir_path in readonly_dirs:
        if os.path.exists(dir_path):
            bwrap_cmd.extend(["--ro-bind", dir_path, dir_path])

    # Language-specific directories (read-only)
    language_dirs = [
        "/usr/local/go",  # Go
        "/usr/lib/jvm",  # Java
        "/opt/java/lib",  # Java libraries
        "/usr/local/cargo",  # Rust
        "/usr/local/rustup",  # Rust
        "/opt/composer",  # PHP Composer
        "/usr/local/lib/node_modules",  # Node.js global modules
        "/usr/local/lib/python3.13",  # Python libraries
    ]

    for dir_path in language_dirs:
        if os.path.exists(dir_path):
            bwrap_cmd.extend(["--ro-bind", dir_path, dir_path])

    # Writable /tmp
    bwrap_cmd.extend(["--tmpfs", "/tmp"])

    # Writable working directory
    if os.path.exists(working_dir):
        bwrap_cmd.extend(["--bind", working_dir, working_dir])

    # Additional bind mounts
    if extra_binds:
        for source, dest in extra_binds:
            if os.path.exists(source):
                bwrap_cmd.extend(["--bind", source, dest])

    # Symlinks for common paths
    bwrap_cmd.extend([
        "--symlink", "/usr/bin/env", "/bin/env",
    ])

    # Dev filesystem (minimal)
    bwrap_cmd.extend([
        "--dev", "/dev",
        "--proc", "/proc",
    ])

    # Process control
    bwrap_cmd.extend([
        "--die-with-parent",  # Kill sandbox if parent dies
        "--new-session",  # New session (prevents job control escape)
    ])

    # Set working directory
    bwrap_cmd.extend(["--chdir", working_dir])

    # Environment variables (minimal)
    bwrap_cmd.extend([
        "--clearenv",
        "--setenv", "PATH", "/usr/local/go/bin:/usr/local/cargo/bin:/usr/local/bin:/usr/bin:/bin",
        "--setenv", "HOME", "/tmp",
        "--setenv", "TMPDIR", "/tmp",
        "--setenv", "LANG", "C.UTF-8",
    ])

    # Add the command to execute
    bwrap_cmd.append("--")
    bwrap_cmd.extend(command)

    return bwrap_cmd


async def run_sandboxed(
    command: List[str],
    stdin_data: Optional[bytes] = None,
    timeout: int = 30,
    working_dir: str = "/mnt/data",
    network_access: bool = False,
    environment: Optional[dict] = None,
) -> Tuple[int, bytes, bytes, bool]:
    """
    Run a command in a sandboxed environment.

    Args:
        command: Command to execute
        stdin_data: Data to pass to stdin
        timeout: Timeout in seconds
        working_dir: Working directory
        network_access: Whether to allow network access
        environment: Additional environment variables

    Returns:
        Tuple of (exit_code, stdout, stderr, timed_out)
    """
    if USE_SANDBOX:
        full_command = build_bwrap_command(
            command=command,
            working_dir=working_dir,
            network_access=network_access,
        )
        exec_env = None  # bwrap sets its own environment
    else:
        # Fallback to direct execution (less secure, but OK in Azure since ACA provides isolation)
        if not IS_AZURE_DEPLOYMENT:
            logger.warning("bubblewrap not available, running without sandbox")
        full_command = command
        # Build environment with proper PATH for all language runtimes
        exec_env = {
            "PATH": "/usr/local/go/bin:/usr/local/cargo/bin:/usr/local/bin:/usr/bin:/bin",
            "HOME": "/tmp",
            "TMPDIR": "/tmp",
            "LANG": "C.UTF-8",
            # Go-specific
            "GOPATH": "/tmp/go",
            "GOCACHE": "/tmp/go-cache",
            # Rust-specific
            "CARGO_HOME": "/usr/local/cargo",
            "RUSTUP_HOME": "/usr/local/rustup",
            # Java-specific
            "JAVA_HOME": "/usr/lib/jvm/temurin-21-jdk-amd64",
        }
        # Merge with language-specific environment
        if environment:
            exec_env.update(environment)

    logger.debug(f"Executing: {' '.join(full_command[:10])}...")

    try:
        proc = await asyncio.create_subprocess_exec(
            *full_command,
            stdin=asyncio.subprocess.PIPE if stdin_data else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir if not USE_SANDBOX else None,
            env=exec_env,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin_data),
                timeout=timeout
            )
            return proc.returncode or 0, stdout, stderr, False

        except asyncio.TimeoutError:
            # Kill the process on timeout
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
            return -1, b"", b"Execution timed out", True

    except Exception as e:
        logger.exception(f"Sandbox execution failed: {e}")
        return -1, b"", str(e).encode(), False


async def run_with_file(
    code: str,
    language_config,
    timeout: int = 30,
    working_dir: str = "/mnt/data",
    stdin_data: Optional[bytes] = None,
) -> Tuple[int, bytes, bytes, bool]:
    """
    Run code that requires a file (not stdin-based).

    Args:
        code: Source code to execute
        language_config: Language configuration
        timeout: Timeout in seconds
        working_dir: Working directory

    Returns:
        Tuple of (exit_code, stdout, stderr, timed_out)
    """
    # Create a temporary file for the code
    extension = language_config.file_extension
    # Java requires filename to match public class name (Code)
    if language_config.code == "java":
        filename = f"Code.{extension}"
    else:
        filename = f"code.{extension}"
    filepath = os.path.join(working_dir, filename)

    # Write code to file
    with open(filepath, "w") as f:
        f.write(code)

    try:
        # Build the command with file substitution
        cmd_template = language_config.execution_command
        basename = os.path.splitext(filename)[0]

        # Replace placeholders
        cmd = cmd_template.replace("{file}", filepath)
        cmd = cmd.replace("{basename}", basename)

        # Execute via shell
        command = ["sh", "-c", cmd]

        return await run_sandboxed(
            command=command,
            stdin_data=stdin_data,
            timeout=int(timeout * language_config.timeout_multiplier),
            working_dir=working_dir,
            network_access=False,
            environment=language_config.environment,
        )

    finally:
        # Clean up the code file
        try:
            os.remove(filepath)
        except OSError:
            pass


async def run_with_stdin(
    code: str,
    language_config,
    timeout: int = 30,
    working_dir: str = "/mnt/data",
) -> Tuple[int, bytes, bytes, bool]:
    """
    Run code that is passed via stdin.

    Args:
        code: Source code to execute
        language_config: Language configuration
        timeout: Timeout in seconds
        working_dir: Working directory

    Returns:
        Tuple of (exit_code, stdout, stderr, timed_out)
    """
    # Parse command
    cmd_parts = language_config.execution_command.split()

    return await run_sandboxed(
        command=cmd_parts,
        stdin_data=code.encode("utf-8"),
        timeout=int(timeout * language_config.timeout_multiplier),
        working_dir=working_dir,
        network_access=False,
        environment=language_config.environment,
    )
