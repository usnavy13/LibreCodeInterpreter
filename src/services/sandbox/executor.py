"""Command execution in nsjail sandboxes.

Replaces ContainerExecutor. Uses asyncio subprocess to invoke nsjail
instead of Docker exec.
"""

import asyncio
import re
import shlex
from typing import Dict, List, Optional, Tuple

import structlog

from ...config import settings
from .nsjail import NsjailConfig, SandboxInfo

logger = structlog.get_logger(__name__)


class SandboxExecutor:
    """Handles command execution inside nsjail sandboxes.

    Replaces ContainerExecutor. Instead of Docker exec, spawns an
    nsjail subprocess for each command execution.
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
        shell_command = ["sh", "-c", command]

        # Build nsjail arguments
        network = settings.enable_wan_access if hasattr(settings, "enable_wan_access") else False
        nsjail_args = self._nsjail_config.build_args(
            sandbox_dir=str(sandbox_info.data_dir),
            command=shell_command,
            language=sandbox_info.language,
            timeout=timeout,
            network=network,
            env=sanitized_env,
        )

        try:
            # Create subprocess
            proc = await asyncio.create_subprocess_exec(
                settings.nsjail_binary,
                *nsjail_args,
                stdin=asyncio.subprocess.PIPE if stdin_payload else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Communicate with timeout
            stdin_data = stdin_payload.encode("utf-8") if stdin_payload else None
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(input=stdin_data),
                    timeout=timeout + 5,  # Grace period beyond nsjail's own limit
                )
            except asyncio.TimeoutError:
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
                    "CLASSPATH": "/mnt/data:/opt/java/lib/*",
                    "JAVA_OPTS": "-Xmx512m -Xms128m",
                    "PATH": "/opt/java/openjdk/bin:/usr/local/bin:/usr/bin:/bin",
                }
            )
        elif normalized_lang == "go":
            env_whitelist.update(
                {
                    "GO111MODULE": "on",
                    "GOPROXY": "https://proxy.golang.org,direct",
                    "GOSUMDB": "sum.golang.org",
                    "GOCACHE": "/mnt/data/go-build",
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
                    "PATH": "/usr/local/cargo/bin:/usr/local/rustup/toolchains/stable-x86_64-unknown-linux-gnu/bin:/usr/local/bin:/usr/bin:/bin",
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
