"""Command execution in Docker containers."""

import asyncio
import re
import shlex
from typing import Any, Dict, Optional, Tuple

import structlog
from docker.errors import DockerException
from docker.models.containers import Container

from ...config import settings
from .utils import wait_for_container_ready, receive_socket_output, run_in_executor

logger = structlog.get_logger(__name__)


class ContainerExecutor:
    """Handles command execution inside Docker containers."""

    def __init__(self, docker_client):
        """Initialize executor with Docker client."""
        self.client = docker_client

    async def execute_command(
        self,
        container: Container,
        command: str,
        timeout: int = None,
        working_dir: Optional[str] = None,
        language: Optional[str] = None,
        stdin_payload: Optional[str] = None,
    ) -> Tuple[int, str, str]:
        """Execute a command in the container with enhanced security."""
        if timeout is None:
            timeout = settings.max_execution_time

        # Ensure container is running
        try:
            container.reload()
            if getattr(container, "status", "") != "running":
                await self._start_container(container)
        except Exception:
            pass

        # Build sanitized environment
        sanitized_env = self._build_sanitized_env(language)
        env_assignments = " ".join(
            [
                f"{key}={self._escape_env_value(value)}"
                for key, value in sanitized_env.items()
            ]
        )

        # Preamble commands
        preamble = "mkdir -p /tmp || true"

        # Build sanitized command
        inner_shell_cmd = shlex.quote(f"{preamble} && {command}")
        if env_assignments:
            sanitized_command = f"env -i {env_assignments} sh -c {inner_shell_cmd}"
        else:
            sanitized_command = f"env -i sh -c {inner_shell_cmd}"

        exec_config = {
            "cmd": ["sh", "-c", sanitized_command],
            "stdout": True,
            "stderr": True,
            "stdin": stdin_payload is not None,
            "tty": False,
            "privileged": False,
        }

        if working_dir:
            exec_config["workdir"] = working_dir

        try:
            return self._execute_via_socket(container, exec_config, stdin_payload, timeout)

        except DockerException as e:
            error_text = str(e)
            logger.error(f"Failed to execute command in container: {error_text}")

            if "is not running" in error_text.lower():
                try:
                    await self._start_container(container)
                    return self._execute_via_socket(
                        container, exec_config, stdin_payload, timeout
                    )
                except Exception as retry_err:
                    logger.error(f"Retry failed: {retry_err}")

            return 1, "", f"Execution failed: {error_text}"
        except Exception as e:
            logger.error(f"Unexpected error during command execution: {e}")
            return 1, "", f"Unexpected execution error: {str(e)}"

    def _execute_via_socket(
        self,
        container: Container,
        exec_config: Dict[str, Any],
        stdin_payload: Optional[str],
        timeout: int,
    ) -> Tuple[int, str, str]:
        """Execute command and collect output via socket."""
        exec_instance = self.client.api.exec_create(container.id, **exec_config)
        exec_id = exec_instance["Id"]

        sock = self.client.api.exec_start(exec_id, socket=True)
        raw_sock = sock._sock
        raw_sock.settimeout(timeout)

        if stdin_payload:
            raw_sock.sendall(stdin_payload.encode("utf-8"))
            raw_sock.shutdown(1)

        output = receive_socket_output(raw_sock)
        exec_info = self.client.api.exec_inspect(exec_id)
        exit_code = exec_info["ExitCode"]

        output_str = self._sanitize_output(output) if output else ""
        stdout, stderr = self._separate_output_streams(output_str, exit_code)

        return exit_code, stdout, stderr

    async def _start_container(self, container: Container) -> bool:
        """Start a container and wait for running state."""
        await run_in_executor(container.start)
        return await wait_for_container_ready(container)

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

    def _separate_output_streams(self, output: str, exit_code: int) -> Tuple[str, str]:
        """Separate stdout and stderr from combined output."""
        if exit_code != 0:
            error_patterns = [
                "error:",
                "Error:",
                "ERROR:",
                "exception:",
                "Exception:",
                "EXCEPTION:",
                "traceback",
                "Traceback",
                "TRACEBACK",
                "failed",
                "Failed",
                "FAILED",
            ]

            lines = output.split("\n")
            stdout_lines = []
            stderr_lines = []

            for line in lines:
                is_error = any(
                    pattern.lower() in line.lower() for pattern in error_patterns
                )
                if is_error:
                    stderr_lines.append(line)
                else:
                    stdout_lines.append(line)

            return "\n".join(stdout_lines), "\n".join(stderr_lines)
        else:
            return output, ""
