"""Command execution in nsjail sandboxes.

Uses asyncio subprocess to invoke nsjail for isolated code execution.
"""

import asyncio
import os
import re
import shlex
import signal
import sysconfig
from typing import Dict, Optional, Tuple

import structlog

from ...config import settings
from .nsjail import NsjailConfig, SandboxInfo

logger = structlog.get_logger(__name__)
DEFAULT_MULTIARCH = sysconfig.get_config_var("MULTIARCH") or "x86_64-linux-gnu"
DEFAULT_PKG_CONFIG_PATH = f"/usr/lib/{DEFAULT_MULTIARCH}/pkgconfig"


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

        # Network access is operator-controlled via ENABLE_SANDBOX_NETWORK.
        # Default off (sandboxes are isolated). When on, sandboxes share the
        # host network namespace so they can reach the inline egress proxy
        # at 127.0.0.1, which then enforces the package-registry allowlist.
        network = bool(settings.enable_sandbox_network)
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
            # Some languages need /proc to function:
            #   - Java needs /proc/self/exe to locate libjli.so.
            #   - Rust needs /proc/self/exe to locate its own binary path.
            #   - Bash sandboxes are the typical entry point for skills (e.g.,
            #     the Anthropic pptx/docx/xlsx skills) that shell out to
            #     LibreOffice (`soffice`) for PDF/image conversion. soffice
            #     hard-fails with "ERROR: /proc not mounted - LibreOffice is
            #     unlikely to work well if at all" without /proc.
            # nsjail still creates a separate PID namespace so the visible
            # /proc is restricted to the sandbox's own processes — main host
            # info disclosure risk is /proc/cpuinfo and /proc/meminfo, which
            # is acceptable in the trusted-tenant model these languages run in.
            lang = sandbox_info.language.lower().strip()
            if lang in ("java", "rs", "bash"):
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
                "unshare",
                "--mount",
                "--",
                "/bin/sh",
                "-c",
                wrapper_cmd,
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

            return proc.returncode or 0, stdout, stderr

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
        deps_root = settings.skill_deps_path  # e.g. /opt/skill-deps

        env_whitelist: Dict[str, str] = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/tmp",
            "TMPDIR": "/tmp",
        }

        if normalized_lang in {"py", "python"}:
            # PYTHONPATH includes the persistent skill-deps cache so installs
            # from earlier executions (or other sessions) are importable. The
            # cache lives under /opt/skill-deps and is mounted from a Docker
            # named volume so it survives container restarts.
            env_whitelist.update(
                {
                    "PYTHONUNBUFFERED": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONPATH": f"{deps_root}/python:/mnt/data",
                    "MPLCONFIGDIR": "/tmp/mplconfig",
                    "XDG_CACHE_HOME": "/tmp/.cache",
                    "MPLBACKEND": "Agg",
                }
            )
        elif normalized_lang in {"js", "ts"}:
            env_whitelist.update(
                {
                    "NODE_PATH": (
                        f"{deps_root}/node/lib/node_modules:/usr/local/lib/node_modules"
                    ),
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
                    "PKG_CONFIG_PATH": os.environ.get(
                        "PKG_CONFIG_PATH", DEFAULT_PKG_CONFIG_PATH
                    ),
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
        # bash and d use default PATH/HOME/TMPDIR only

        # When sandbox network access is enabled, route outbound HTTPS through
        # the inline egress proxy (allowlist-enforced) and point EVERY
        # package manager at the persistent skill-deps cache. We set all of
        # these regardless of `language` because skills routinely shell out
        # — a bash skill might `pip install`, `npm install -g`, `go get`,
        # etc. Limiting these to the matching language broke the bash case
        # (no NPM_CONFIG_PREFIX → `npm -g` tries /usr/lib/node_modules).
        # The proxy listens on 127.0.0.1 inside the API container's network
        # namespace; sandboxes share that namespace via nsjail's
        # --disable_clone_newnet so 127.0.0.1 reaches the proxy.
        if settings.enable_sandbox_network:
            proxy_url = f"http://127.0.0.1:{settings.sandbox_egress_port}"
            env_whitelist.update(
                {
                    "HTTPS_PROXY": proxy_url,
                    "https_proxy": proxy_url,
                    "HTTP_PROXY": proxy_url,
                    "http_proxy": proxy_url,
                    "NO_PROXY": "127.0.0.1,localhost",
                    "no_proxy": "127.0.0.1,localhost",
                    # Python: pip installs land in the persistent cache.
                    "PIP_TARGET": f"{deps_root}/python",
                    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                    # Node: -g installs land in the persistent cache.
                    "NPM_CONFIG_PREFIX": f"{deps_root}/node",
                    "NPM_CONFIG_CACHE": f"{deps_root}/node/.npm-cache",
                    # Go: module cache is persistent.
                    "GOPATH": f"{deps_root}/go",
                    "GOMODCACHE": f"{deps_root}/go/pkg/mod",
                    # Rust: crates.io cache is persistent.
                    "CARGO_HOME": f"{deps_root}/cargo",
                }
            )
            # Make installed binaries immediately usable on PATH (npm -g, pip
            # console scripts, cargo bins). Prepend so they win over system
            # equivalents inside the sandbox.
            env_whitelist["PATH"] = (
                f"{deps_root}/node/bin:{deps_root}/python/bin:"
                f"{deps_root}/cargo/bin:{deps_root}/go/bin:"
                f"{env_whitelist['PATH']}"
            )
            # Runtime import paths so freshly-installed packages are loadable
            # without further config. These have to be set for EVERY language
            # (not just py/js) because skills routinely shell out — a bash
            # skill might `node -e "require('foo')"` after `npm install -g foo`.
            # If a language already set its own PYTHONPATH/NODE_PATH above,
            # prepend the deps cache so it wins for newly-installed packages.
            existing_pythonpath = env_whitelist.get("PYTHONPATH", "")
            env_whitelist["PYTHONPATH"] = (
                f"{deps_root}/python:{existing_pythonpath}"
                if existing_pythonpath
                else f"{deps_root}/python:/mnt/data"
            )
            existing_node_path = env_whitelist.get("NODE_PATH", "")
            node_dep_path = f"{deps_root}/node/lib/node_modules"
            env_whitelist["NODE_PATH"] = (
                f"{node_dep_path}:{existing_node_path}"
                if existing_node_path
                else f"{node_dep_path}:/usr/local/lib/node_modules"
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
