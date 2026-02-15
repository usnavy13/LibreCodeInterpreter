"""nsjail configuration and sandbox info dataclass.

SandboxInfo replaces docker.models.containers.Container as the
handle for a running sandbox. NsjailConfig builds the CLI arguments
for invoking nsjail.
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

    This replaces docker.models.containers.Container as the handle
    used throughout the codebase to reference a running execution
    environment.
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
        args.append("--quiet")

        # Time limit (0 = no limit for REPL mode)
        if repl_mode:
            args.extend(["--time_limit", "0"])
        else:
            args.extend(["--time_limit", str(timeout)])

        # Resource limits
        args.extend(["--rlimit_as", str(settings.max_memory_mb)])
        args.extend(
            ["--cgroup_mem_max", str(settings.max_memory_mb * 1024 * 1024)]
        )
        args.extend(["--cgroup_pids_max", str(settings.max_pids)])

        # Namespace isolation
        args.append("--clone_newpid")
        args.append("--clone_newns")
        if not network:
            args.append("--clone_newnet")
        args.append("--clone_newipc")
        args.append("--clone_newuts")

        # Hostname
        args.extend(["--hostname", "sandbox"])

        # Security
        args.extend(["--keep_caps", "false"])
        args.append("--disable_proc")

        # Read-only system bind mounts
        system_ro_mounts = [
            "/usr:/usr",
            "/lib:/lib",
            "/lib64:/lib64",
            "/bin:/bin",
            "/sbin:/sbin",
            "/etc/alternatives:/etc/alternatives",
            "/etc/ld.so.cache:/etc/ld.so.cache",
            "/etc/ld.so.conf:/etc/ld.so.conf",
            "/etc/ld.so.conf.d:/etc/ld.so.conf.d",
            "/etc/passwd:/etc/passwd",
            "/etc/group:/etc/group",
            "/etc/nsswitch.conf:/etc/nsswitch.conf",
        ]
        for mount in system_ro_mounts:
            args.extend(["--bindmount_ro", mount])

        # Per-language runtime bind mounts (read-only)
        lang_mounts = self._LANGUAGE_BIND_MOUNTS.get(normalized_lang, [])
        for mount_path in lang_mounts:
            args.extend(["--bindmount_ro", f"{mount_path}:{mount_path}"])

        # Writable workspace
        args.extend(["--bindmount", f"{sandbox_dir}:/mnt/data"])

        # tmpfs for /tmp
        args.extend(
            ["--tmpfsmount", f"/tmp:size={tmpfs_size_mb * 1024 * 1024}"]
        )

        # Working directory
        args.extend(["--cwd", "/mnt/data"])

        # User/group
        args.extend(["--uid", str(user_id)])
        args.extend(["--gid", str(user_id)])

        # Environment variables
        if env:
            for key, value in env.items():
                args.extend(["--env", f"{key}={value}"])

        # Separator between nsjail args and the command
        args.append("--")

        # Append the actual command
        args.extend(command)

        return args
