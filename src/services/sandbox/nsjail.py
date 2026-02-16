"""nsjail configuration and sandbox info dataclass.

SandboxInfo is the handle for a running sandbox. NsjailConfig builds
the CLI arguments for invoking nsjail.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import structlog

from ...config import settings
from ...config.languages import get_user_id_for_language

logger = structlog.get_logger(__name__)


@dataclass
class SandboxInfo:
    """Represents an nsjail sandbox instance.

    This is the handle used throughout the codebase to reference a
    running execution environment.
    """

    sandbox_id: str
    sandbox_dir: Path
    data_dir: Path  # Host dir bind-mounted as /mnt/data
    language: str
    session_id: str
    created_at: datetime
    repl_mode: bool = False
    labels: Dict[str, str] = field(default_factory=dict)

    @property
    def id(self) -> str:
        """Compatibility property matching Container.id."""
        return self.sandbox_id


class NsjailConfig:
    """Builds nsjail CLI arguments from settings.

    Translates the application's security and resource settings into
    the corresponding nsjail command-line flags.
    """

    # Per-language read-only bind mounts for runtime paths
    _LANGUAGE_BIND_MOUNTS: Dict[str, List[str]] = {
        "py": [
            "/usr/local/lib/python3",
            "/usr/local/bin/python3",
            "/usr/local/bin/python",
        ],
        "js": [
            "/usr/local/bin/node",
            "/usr/local/lib/node_modules",
        ],
        "ts": [
            "/usr/local/bin/node",
            "/usr/local/bin/tsc",
            "/usr/local/lib/node_modules",
        ],
        "go": [
            "/usr/local/go",
        ],
        "java": [
            "/opt/java",
            "/usr/lib/jvm",
        ],
        "c": [],
        "cpp": [],
        "php": [
            "/usr/local/etc/php",
            "/usr/local/bin/php",
            "/usr/local/lib/php",
        ],
        "rs": [
            "/usr/local/cargo",
            "/usr/local/rustup",
        ],
        "r": [
            "/usr/local/lib/R",
            "/usr/lib/R",
        ],
        "f90": [],
        "d": [
            "/usr/lib/ldc",
            "/usr/bin/ldc2",
            "/usr/bin/ldmd2",
        ],
    }

    def __init__(self):
        pass

    def build_args(
        self,
        sandbox_dir: str,
        command: List[str],
        language: str,
        timeout: int = None,
        network: bool = False,
        repl_mode: bool = False,
        env: Optional[Dict[str, str]] = None,
    ) -> List[str]:
        """Build nsjail CLI arguments.

        Args:
            sandbox_dir: Host directory to bind-mount as /mnt/data
            command: Command and arguments to execute inside the sandbox
            language: Programming language code
            timeout: Execution timeout in seconds
            network: Whether to allow network access
            repl_mode: Whether this is a REPL session (affects timeout)
            env: Environment variables to set inside the sandbox

        Returns:
            List of nsjail CLI arguments (not including "nsjail" itself)
        """
        if timeout is None:
            timeout = settings.max_execution_time

        normalized_lang = language.lower().strip()
        user_id = get_user_id_for_language(normalized_lang)
        tmpfs_size_mb = settings.sandbox_tmpfs_size_mb

        args: List[str] = []

        # Execution mode
        args.extend(["--mode", "o"])

        # Suppress nsjail diagnostic output
        args.append("--really_quiet")

        # REPL mode: skip setsid() so stdin pipes stay connected.
        # By default nsjail calls setsid() which creates a new session
        # and detaches the child from the pipe's session, breaking stdin.
        if repl_mode:
            args.append("--skip_setsid")

        # Time limit (0 = no limit for REPL mode)
        if repl_mode:
            args.extend(["--time_limit", "0"])
        else:
            args.extend(["--time_limit", str(timeout)])

        # Per-process resource limits (rlimits)
        args.extend(
            ["--rlimit_as", "hard"]
        )  # Virtual address space (Go needs unlimited)
        args.extend(["--rlimit_fsize", "100"])  # Max file size: 100MB
        args.extend(["--rlimit_nofile", "256"])  # Max open files
        args.extend(
            ["--rlimit_nproc", "256"]
        )  # Max processes (needs headroom for REPL module imports)

        # Note: per-sandbox cgroup limits are not used because the
        # containerized environment prevents nsjail from writing to cgroup.procs.
        # Memory/CPU limits are enforced at the API container level via compose
        # deploy.resources. Per-process rlimits above provide additional
        # per-sandbox enforcement for file size, open files, and process count.

        # Namespace configuration:
        # - User namespace disabled: avoids /proc/self/gid_map write errors
        #   in the containerized environment. Security is still enforced by
        #   PID/mount/net/IPC/UTS namespaces and capability dropping.
        # - Network namespace enabled by default (disables network access).
        # - Mount namespace uses --no_pivotroot with --chroot / since
        #   pivot_root fails in nested container environments.
        args.append("--disable_clone_newuser")
        if not network:
            # Network isolation: new net namespace with no interfaces
            args.append("--iface_no_lo")
        else:
            # Allow network: skip creating a new network namespace
            args.append("--disable_clone_newnet")

        # Mount namespace: disabled for nsjail itself. The executor wraps nsjail
        # in `unshare --mount` + `mount --bind` to map sandbox_dir to /mnt/data.
        # This gives each execution its own mount namespace where /mnt/data points
        # to the correct sandbox dir (concurrent-safe).
        args.append("--disable_clone_newns")

        # Hostname
        args.extend(["--hostname", "sandbox"])

        # Security: do NOT use --keep_caps (that flag KEEPS caps).
        # By default nsjail drops all capabilities, which is what we want.
        args.append("--disable_proc")

        # Seccomp policy: block dangerous syscalls
        # - ptrace: prevents process inspection/debugging (BUG-006a)
        # - bind: prevents opening server sockets even with network access (BUG-006c)
        # Using ERRNO(1) so the process gets EPERM rather than SIGSYS
        args.extend(
            [
                "--seccomp_string",
                "POLICY policy { ERRNO(1) { ptrace, bind } } USE policy DEFAULT ALLOW",
            ]
        )

        # Working directory: /mnt/data (bind-mounted by the executor wrapper)
        args.extend(["--cwd", "/mnt/data"])

        # User/group
        args.extend(["--user", str(user_id)])
        args.extend(["--group", str(user_id)])

        # Environment variables
        if env:
            for key, value in env.items():
                args.extend(["--env", f"{key}={value}"])

        # Separator between nsjail args and the command
        args.append("--")

        # Append the actual command
        args.extend(command)

        return args
