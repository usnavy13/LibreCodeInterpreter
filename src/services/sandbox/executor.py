"""Command execution in nsjail sandboxes.

Uses asyncio subprocess to invoke nsjail for isolated code execution.
"""

import asyncio
import os
import re
import shlex
import signal
from typing import Dict, List, Optional, Tuple

import structlog

from ...config import settings
from .nsjail import NsjailConfig, SandboxInfo

logger = structlog.get_logger(__name__)


class SandboxExecutor:
    """Handles command execution inside nsjail sandboxes.

    Spawns an nsjail subprocess for each command execution.
    """

    def __init__(self, nsjail_config: NsjailConfig):
        """Initialize executor with nsjail config.

        Args:
            nsjail_config: Configuration for building nsjail arguments
        """
        self._nsjail_config = nsjail_config

    async def execute_command(
        self,
        sandbox_info: SandboxInfo,
        command: str,
        timeout: int = None,
        language: Optional[str] = None,
        stdin_payload: Optional[str] = None,
    ) -> Tuple[int, str, str]:
        """Execute a command in the sandbox via nsjail.

        Args:
            sandbox_info: Sandbox to execute in
            command: Command string to execute
            timeout: Maximum execution time in seconds
            language: Programming language code
            stdin_payload: Optional stdin data

        Returns:
            Tuple of (exit_code, stdout, stderr)
        """
        if timeout is None:
            timeout = settings.max_execution_time

        # Build sanitized environment
        sanitized_env = self._build_sanitized_env(language)

        # Wrap the command in a shell for consistent behavior
        # Use absolute path since nsjail uses execve (no PATH search)
        shell_command = ["/bin/sh", "-c", command]

        # Build nsjail arguments
        network = False  # nsjail sandboxes run without network access
        nsjail_args = self._nsjail_config.build_args(
            sandbox_dir=str(sandbox_info.data_dir),
            command=shell_command,
            language=sandbox_info.language,
            timeout=timeout,
            network=network,
            env=sanitized_env,
        )

        try:
            # Wrap nsjail in unshare+mount to bind sandbox_dir to /mnt/data.
            # This gives each execution its own mount namespace so /mnt/data
            # resolves to the correct sandbox dir (concurrent-safe).
            nsjail_cmd = " ".join(
                shlex.quote(str(a)) for a in [settings.nsjail_binary] + nsjail_args
            )
            # BUG-003: Mask /proc for most languages.
            # Java and Rust need /proc/self/exe to locate shared libraries
            # (JVM needs libjli.so, rustc needs its own binary path).
            # For these languages, /proc remains accessible (known limitation).
            lang = sandbox_info.language.lower().strip()
            if lang in ("java", "rs"):
                proc_mask = ""
            else:
                proc_mask = "mount --bind /tmp/empty_proc /proc && "

            wrapper_cmd = (
                # Bind sandbox dir to /mnt/data (before hiding sandboxes dir)
                f"mount --bind {shlex.quote(str(sandbox_info.data_dir))} /mnt/data && "
                # BUG-001: Hide other sessions' sandbox directories
                f"mount -t tmpfs -o size=1k tmpfs /var/lib/code-interpreter/sandboxes && "
                # BUG-002: Hide metrics database
                f"mount -t tmpfs -o size=1k tmpfs /app/data && "
                # BUG-004: Hide log directory
                f"mount -t tmpfs -o size=1k tmpfs /var/log && "
                # BUG-005: Hide SSL certs and application source
                f"mount -t tmpfs -o size=1k tmpfs /app/ssl && "
                f"mount -t tmpfs -o size=1k tmpfs /app/dashboard && "
                f"mount -t tmpfs -o size=1k tmpfs /app/src && "
                # BUG-003: Hide /proc (except Java which needs /proc/self/exe)
                f"{proc_mask}"
                # Execute nsjail
                f"{nsjail_cmd}"
            )

            # Create subprocess via unshare --mount for per-process mount namespace
            proc = await asyncio.create_subprocess_exec(
                "unshare", "--mount", "--", "/bin/sh", "-c", wrapper_cmd,
                stdin=asyncio.subprocess.PIPE if stdin_payload else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,  # New process group for clean cleanup
            )

            # Communicate with timeout
            stdin_data = stdin_payload.encode("utf-8") if stdin_payload else None
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(input=stdin_data),
                    timeout=timeout + 5,  # Grace period beyond nsjail's own limit
                )
            except asyncio.TimeoutError:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    proc.kill()
                await proc.wait()
                logger.warning(
                    "Sandbox execution timed out",
                    sandbox_id=sandbox_info.sandbox_id[:12],
                    timeout=timeout,
                )
                return 124, "", f"Execution timed out after {timeout} seconds"

            # Sanitize output
            stdout = self._sanitize_output(stdout_bytes) if stdout_bytes else ""
            stderr = self._sanitize_output(stderr_bytes) if stderr_bytes else ""

            return proc.returncode, stdout, stderr

        except Exception as e:
            logger.error(
                "Sandbox execution failed",
                sandbox_id=sandbox_info.sandbox_id[:12],
                error=str(e),
            )
            return 1, "", f"Execution failed: {str(e)}"

    def _build_sanitized_env(self, language: Optional[str]) -> Dict[str, str]:
        """Build environment whitelist for execution."""
        normalized_lang = (language or "").lower().strip()

        env_whitelist: Dict[str, str] = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/tmp",
            "TMPDIR": "/tmp",
        }

        if normalized_lang in {"py", "python"}:
            env_whitelist.update(
                {
                    "PYTHONUNBUFFERED": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONPATH": "/mnt/data",
                    "MPLCONFIGDIR": "/tmp/mplconfig",
                    "XDG_CACHE_HOME": "/tmp/.cache",
                    "MPLBACKEND": "Agg",
                }
            )
        elif normalized_lang in {"js", "ts"}:
            env_whitelist.update(
                {
                    "NODE_PATH": "/usr/local/lib/node_modules",
                }
            )
        elif normalized_lang == "java":
            env_whitelist.update(
                {
                    "CLASSPATH": ".:/opt/java/lib/*",
                    "JAVA_OPTS": "-Xmx512m -Xms128m",
                    "PATH": "/opt/java/openjdk/bin:/usr/local/bin:/usr/bin:/bin",
                }
            )
        elif normalized_lang == "go":
            env_whitelist.update(
                {
                    "GO111MODULE": "on",
                    "GOROOT": "/usr/local/go",
                    "GOPROXY": "https://proxy.golang.org,direct",
                    "GOSUMDB": "sum.golang.org",
                    "GOCACHE": "/tmp/go-build",
                    "PATH": "/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin",
                }
            )
        elif normalized_lang in {"c", "cpp"}:
            env_whitelist.update(
                {
                    "CC": "gcc",
                    "CXX": "g++",
                    "PKG_CONFIG_PATH": "/usr/lib/x86_64-linux-gnu/pkgconfig",
                }
            )
        elif normalized_lang == "php":
            env_whitelist.update(
                {
                    "PHP_INI_SCAN_DIR": "/usr/local/etc/php/conf.d",
                    "COMPOSER_HOME": "/opt/composer/global",
                    "PATH": "/opt/composer/global/vendor/bin:/usr/local/bin:/usr/bin:/bin",
                }
            )
        elif normalized_lang == "rs":
            env_whitelist.update(
                {
                    "CARGO_HOME": "/usr/local/cargo",
                    "RUSTUP_HOME": "/usr/local/rustup",
                    "PATH": "/usr/local/cargo/bin:/usr/local/bin:/usr/bin:/bin",
                }
            )
        elif normalized_lang == "r":
            env_whitelist.update(
                {
                    "R_LIBS_USER": "/usr/local/lib/R/site-library",
                }
            )
        elif normalized_lang == "f90":
            env_whitelist.update(
                {
                    "FORTRAN_COMPILER": "gfortran",
                    "FC": "gfortran",
                    "F77": "gfortran",
                    "F90": "gfortran",
                    "F95": "gfortran",
                }
            )

        return env_whitelist

    def _escape_env_value(self, value: str) -> str:
        """Escape env var values for shell."""
        try:
            safe = str(value).replace("'", "'\\''")
            return f"'{safe}'"
        except Exception:
            return "''"

    def _sanitize_output(self, output: bytes) -> str:
        """Sanitize command output for security."""
        try:
            output_str = output.decode("utf-8", errors="replace")

            max_output_size = 1024 * 1024  # 1MB limit
            if len(output_str) > max_output_size:
                output_str = (
                    output_str[:max_output_size]
                    + "\n[Output truncated - size limit exceeded]"
                )

            output_str = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", output_str)
            return output_str

        except Exception as e:
            logger.error(f"Failed to sanitize output: {e}")
            return "[Output sanitization failed]"
