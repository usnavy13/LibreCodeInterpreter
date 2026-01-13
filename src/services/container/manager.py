"""Container lifecycle management."""

import asyncio
import io
import json
import tarfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import structlog
import docker.types
from docker.errors import DockerException, ImageNotFound
from docker.models.containers import Container

from ...config import settings
from ...config.languages import (
    get_user_id_for_language,
)
from .client import DockerClientFactory
from .executor import ContainerExecutor
from .utils import wait_for_container_ready, run_in_executor

logger = structlog.get_logger(__name__)


class ContainerManager:
    """Manages Docker container lifecycle operations."""

    def __init__(self):
        """Initialize the container manager."""
        self._client_factory = DockerClientFactory()
        self._executor: Optional[ContainerExecutor] = None

    @property
    def client(self):
        """Get the Docker client."""
        return self._client_factory.get_client()

    @property
    def executor(self) -> ContainerExecutor:
        """Get the container executor."""
        if self._executor is None and self.client:
            self._executor = ContainerExecutor(self.client)
        return self._executor

    def is_available(self) -> bool:
        """Check if Docker is available."""
        return self._client_factory.is_available()

    def get_initialization_error(self) -> Optional[str]:
        """Get Docker initialization error if any."""
        return self._client_factory.get_initialization_error()

    def reset_initialization(self) -> None:
        """Reset initialization state."""
        self._client_factory.reset_initialization()
        self._executor = None

    def get_image_for_language(self, language: str) -> str:
        """Get Docker image for a programming language.

        Uses fallback logic to find available images:
        1. Configured image from settings/env (e.g., DOCKER_IMAGE_REGISTRY)
        2. Local build prefix: code-interpreter/<lang>:latest
        3. GHCR prefix: ghcr.io/usnavy13/librecodeinterpreter/<lang>:latest
        """
        lang = language.lower().strip()

        # Get the configured image name
        configured_image = settings.get_image_for_language(lang)

        # Build list of fallback images to try
        # Extract the language-specific part (e.g., "python" from "registry/python:tag")
        lang_part = configured_image.split("/")[-1]  # e.g., "python:latest"

        fallback_images = [
            configured_image,  # First: configured image
            f"code-interpreter/{lang_part}",  # Second: local build
            f"ghcr.io/usnavy13/librecodeinterpreter/{lang_part}",  # Third: GHCR
        ]

        # Remove duplicates while preserving order
        seen = set()
        unique_images = []
        for img in fallback_images:
            if img not in seen:
                seen.add(img)
                unique_images.append(img)

        # Check which image exists locally
        if self.is_available():
            for image in unique_images:
                try:
                    self.client.images.get(image)
                    if image != configured_image:
                        logger.info(f"Using fallback image {image} for language {lang}")
                    return image
                except ImageNotFound:
                    continue
                except Exception:
                    continue

            # No local image found - fail fast with clear error
            tried_images = ", ".join(unique_images)
            error_msg = (
                f"No Docker image found for language '{lang}'. "
                f"Tried: {tried_images}. "
                f"Please build images with 'docker compose build' or pull from GHCR."
            )
            logger.error(error_msg)
            raise ImageNotFound(error_msg)

        # Docker not available, return configured (will fail later with better error)
        return configured_image

    def get_user_id_for_language(self, language: str) -> int:
        """Get the user ID for a language container."""
        return get_user_id_for_language(language.lower().strip())

    async def pull_image_if_needed(self, image: str) -> bool:
        """Pull Docker image if not available locally."""
        if not self.is_available():
            logger.error(f"Cannot pull image {image}: Docker not available")
            return False

        try:
            self.client.images.get(image)
            return True
        except ImageNotFound:
            logger.info(f"Pulling Docker image: {image}")
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self.client.images.pull, image)
                logger.info(f"Successfully pulled image: {image}")
                return True
            except DockerException as e:
                logger.error(f"Failed to pull image {image}: {e}")
                return False
        except Exception as e:
            logger.error(f"Unexpected error checking/pulling image {image}: {e}")
            return False

    def create_container(
        self,
        image: str,
        session_id: str,
        command: Optional[str] = None,
        working_dir: str = "/mnt/data",
        environment: Optional[Dict[str, str]] = None,
        language: Optional[str] = None,
        repl_mode: bool = False,
    ) -> Container:
        """Create a new Docker container.

        Args:
            image: Docker image to use
            session_id: Session identifier for the container
            command: Optional command to run (overrides default)
            working_dir: Working directory inside container
            environment: Optional environment variables
            language: Programming language for this container
            repl_mode: If True, start container with REPL server for fast execution
        """
        if not self.is_available():
            error_msg = f"Cannot create container: Docker not available"
            if self.get_initialization_error():
                error_msg += f" - {self.get_initialization_error()}"
            raise DockerException(error_msg)

        container_name = f"ci-exec-{session_id[:12]}-{uuid.uuid4().hex[:8]}"

        # Build environment variables
        env = environment.copy() if environment else {}
        if repl_mode:
            env["REPL_MODE"] = "true"

        # Determine network configuration
        use_wan_access = settings.enable_wan_access

        # Security hardening: paths to mask to prevent host info leakage
        # Note: MaskedPaths/ReadonlyPaths are not supported by docker-py 7.1.0.
        # Instead, we use bind mounts to /dev/null for critical paths like
        # /proc/kallsyms and /proc/modules (see "mounts" in container_config).
        # The list below is kept for documentation purposes.
        hardening_config: Dict[str, Any] = {}
        if settings.container_mask_host_info:
            hardening_config["masked_paths"] = [
                "/proc/version",  # Kernel version (reveals Azure hosting)
                "/proc/version_signature",
                "/proc/cpuinfo",  # CPU count and model
                "/proc/meminfo",  # Total RAM
                "/proc/kcore",
                "/proc/keys",
                "/proc/timer_list",
                "/proc/sched_debug",
                "/proc/kallsyms",  # Kernel symbol addresses (KASLR bypass) - masked via bind mount
                "/proc/modules",  # Loaded kernel modules - masked via bind mount
                "/sys/firmware",
                "/sys/kernel/security",
                "/etc/machine-id",  # Unique machine identifier
                "/var/lib/dbus/machine-id",
            ]
            hardening_config["readonly_paths"] = [
                "/proc/bus",
                "/proc/fs",
                "/proc/irq",
                "/proc/sys",
                "/proc/sysrq-trigger",
            ]

        # Build labels
        labels = {
            "com.code-interpreter.managed": "true",
            "com.code-interpreter.type": "execution",
            "com.code-interpreter.session-id": session_id,
            "com.code-interpreter.language": language or "unknown",
            "com.code-interpreter.created-at": datetime.utcnow().isoformat(),
            "com.code-interpreter.repl-mode": "true" if repl_mode else "false",
            "com.code-interpreter.wan-access": "true" if use_wan_access else "false",
        }

        # Determine command and entrypoint
        container_command: Any = command
        entrypoint_override = None
        if not command:
            container_command = ["tail", "-f", "/dev/null"]
            try:
                image_lower = (image or "").lower()
                if "dlang2/dmd-ubuntu" in image_lower or image_lower.startswith(
                    "dlang2/"
                ):
                    entrypoint_override = ["/bin/sh", "-c"]
                    container_command = "while true; do sleep 3600; done"
            except Exception:
                pass

        # Build security options with seccomp profile
        security_opts = list(settings.docker_security_opt)
        if settings.docker_seccomp_profile:
            # Resolve profile path (relative to project root or absolute)
            profile_path = Path(settings.docker_seccomp_profile)
            if not profile_path.is_absolute():
                # Relative to project root (4 levels up from this file)
                project_root = Path(__file__).parent.parent.parent.parent
                profile_path = project_root / profile_path
            if profile_path.exists():
                try:
                    with open(profile_path) as f:
                        seccomp_data = json.load(f)
                    # docker-py accepts inline JSON via seccomp=<json_string>
                    security_opts.append(f"seccomp={json.dumps(seccomp_data)}")
                    logger.debug(
                        "Loaded seccomp profile",
                        profile=str(profile_path),
                        blocked_syscalls=len(seccomp_data.get("syscalls", [])),
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to load seccomp profile, using default",
                        profile=str(profile_path),
                        error=str(e),
                    )
            else:
                logger.warning(
                    "Seccomp profile not found, using default",
                    profile=str(profile_path),
                )

        # Build container config
        # Security hardening applied:
        # - seccomp profile: blocks dangerous syscalls (ptrace, etc.)
        # - ulimits: nofile limits to prevent FD exhaustion
        # - pids_limit: prevents fork bombs
        container_config: Dict[str, Any] = {
            "image": image,
            "name": container_name,
            "working_dir": working_dir,
            "detach": True,
            "stdin_open": True,
            "tty": False if repl_mode else True,
            "mem_limit": f"{settings.max_memory_mb}m",
            "memswap_limit": f"{settings.max_memory_mb}m",
            "nano_cpus": int(settings.max_cpus * 1e9),
            "security_opt": security_opts,
            "cap_drop": ["ALL"],
            "cap_add": ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"],
            # read_only must be False to allow file uploads to /mnt/data
            "read_only": False,
            "tmpfs": settings.docker_tmpfs,
            # pids_limit: cgroup-based per-container process limit (prevents fork bombs)
            "pids_limit": settings.max_pids,
            "ulimits": [
                docker.types.Ulimit(
                    name="nofile",
                    soft=settings.max_open_files,
                    hard=settings.max_open_files,
                ),
            ],
            # Note: /proc/kallsyms and /proc/modules masking requires MaskedPaths
            # which docker-py doesn't support. These paths are read-only by default.
            "environment": env,
            "labels": labels,
            "hostname": settings.container_generic_hostname,
            "domainname": "",
            "command": container_command,
        }

        if entrypoint_override:
            container_config["entrypoint"] = entrypoint_override

        # Configure network access
        if use_wan_access:
            container_config["network"] = settings.wan_network_name
            container_config["dns"] = settings.wan_dns_servers
            container_config["dns_search"] = []
            container_config["dns_opt"] = ["ndots:1"]
        else:
            container_config["network_mode"] = "none"

        try:
            container = self.client.containers.create(**container_config)
            logger.info(
                f"Created container {container.id[:12]} for session {session_id}"
            )
            return container
        except DockerException as e:
            logger.error(f"Failed to create container for session {session_id}: {e}")
            raise

    async def start_container(self, container: Container) -> bool:
        """Start a Docker container."""
        try:
            await run_in_executor(container.start)
            return await wait_for_container_ready(container)
        except DockerException as e:
            logger.error(f"Failed to start container {container.id[:12]}: {e}")
            return False

    async def execute_command(
        self,
        container: Container,
        command: str,
        timeout: int = None,
        working_dir: Optional[str] = None,
        language: Optional[str] = None,
        stdin_payload: Optional[str] = None,
    ) -> Tuple[int, str, str]:
        """Execute a command in the container."""
        return await self.executor.execute_command(
            container, command, timeout, working_dir, language, stdin_payload
        )

    async def copy_to_container(
        self, container: Container, source_path: str, dest_path: str
    ) -> bool:
        """Copy file to container from disk path."""
        try:
            with open(source_path, "rb") as f:
                data = f.read()
            return await self.copy_content_to_container(container, data, dest_path)
        except Exception as e:
            logger.error(f"Failed to copy file to container: {e}")
            return False

    async def copy_content_to_container(
        self, container: Container, content: bytes, dest_path: str
    ) -> bool:
        """Copy content directly to container without tempfiles.

        This is the optimized path that avoids disk I/O by streaming
        content directly to the container via in-memory tar archive.

        Args:
            container: Target container
            content: File content as bytes
            dest_path: Destination path in container (e.g., /mnt/data/file.py)

        Returns:
            True if successful, False otherwise
        """
        try:
            loop = asyncio.get_event_loop()

            # Build in-memory tar archive
            tar_buffer = io.BytesIO()
            with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
                tarinfo = tarfile.TarInfo(name=dest_path.split("/")[-1])
                tarinfo.size = len(content)
                tarinfo.mode = 0o644
                tar.addfile(tarinfo, io.BytesIO(content))

            tar_buffer.seek(0)

            # Stream directly to container
            dest_dir = "/".join(dest_path.split("/")[:-1]) or "/"
            await loop.run_in_executor(
                None,
                lambda: container.put_archive(
                    path=dest_dir, data=tar_buffer.getvalue()
                ),
            )

            return True
        except Exception as e:
            logger.error(f"Failed to copy content to container: {e}")
            return False

    async def copy_from_container(
        self, container: Container, source_path: str, dest_path: str
    ) -> bool:
        """Copy file from container to disk."""
        try:
            content = await self.get_file_content_from_container(container, source_path)
            if content is not None:
                with open(dest_path, "wb") as f:
                    f.write(content)
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to copy file from container: {e}")
            return False

    async def get_file_content_from_container(
        self, container: Container, source_path: str
    ) -> Optional[bytes]:
        """Get file content directly from container without tempfiles.

        This is the optimized path that avoids disk I/O by extracting
        content directly from the container's tar archive to memory.

        Args:
            container: Source container
            source_path: Path to file in container

        Returns:
            File content as bytes, or None if failed
        """
        try:
            loop = asyncio.get_event_loop()

            archive_data, _ = await loop.run_in_executor(
                None, lambda: container.get_archive(source_path)
            )

            archive_bytes = b"".join(archive_data)
            tar_buffer = io.BytesIO(archive_bytes)

            with tarfile.open(fileobj=tar_buffer, mode="r") as tar:
                member = tar.next()
                if member:
                    file_data = tar.extractfile(member)
                    if file_data:
                        return file_data.read()

            return None
        except Exception as e:
            logger.error(f"Failed to get file content from container: {e}")
            return None

    async def get_container_stats(
        self, container: Container
    ) -> Optional[Dict[str, Any]]:
        """Get container resource usage statistics."""
        try:
            loop = asyncio.get_event_loop()
            stats = await loop.run_in_executor(
                None, lambda: container.stats(stream=False)
            )

            memory_stats = stats.get("memory_stats", {})
            cpu_stats = stats.get("cpu_stats", {})

            return {
                "memory_usage_mb": memory_stats.get("usage", 0) / (1024 * 1024),
                "memory_limit_mb": memory_stats.get("limit", 0) / (1024 * 1024),
                "cpu_usage_percent": self._calculate_cpu_percent(
                    cpu_stats, stats.get("precpu_stats", {})
                ),
                "timestamp": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            logger.error(f"Failed to get container stats: {e}")
            return None

    def _calculate_cpu_percent(self, cpu_stats: Dict, precpu_stats: Dict) -> float:
        """Calculate CPU usage percentage."""
        try:
            cpu_delta = cpu_stats.get("cpu_usage", {}).get(
                "total_usage", 0
            ) - precpu_stats.get("cpu_usage", {}).get("total_usage", 0)

            system_delta = cpu_stats.get("system_cpu_usage", 0) - precpu_stats.get(
                "system_cpu_usage", 0
            )

            if system_delta > 0 and cpu_delta > 0:
                cpu_count = len(cpu_stats.get("cpu_usage", {}).get("percpu_usage", [1]))
                return (cpu_delta / system_delta) * cpu_count * 100.0

            return 0.0
        except (KeyError, ZeroDivisionError):
            return 0.0

    async def stop_container(self, container: Container, timeout: int = 2) -> bool:
        """Stop a container."""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: container.stop(timeout=timeout))
            return True
        except DockerException as e:
            logger.error(f"Failed to stop container {container.id[:12]}: {e}")
            return False

    async def remove_container(self, container: Container, force: bool = True) -> bool:
        """Remove a container."""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: container.remove(force=force))
            return True
        except DockerException as e:
            logger.error(f"Failed to remove container {container.id[:12]}: {e}")
            return False

    async def force_kill_container(self, container: Container) -> bool:
        """Force kill and remove a container."""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: container.remove(force=True))
            return True
        except DockerException as e:
            logger.error(f"Failed to force kill container {container.id[:12]}: {e}")
            return False

    async def force_kill_containers_batch(
        self, containers: List[Container], chunk_size: int = 50
    ) -> int:
        """Force kill containers in batch."""
        if not containers or not self.is_available():
            return 0

        logger.info(f"Batch force kill of {len(containers)} containers")
        start_time = datetime.utcnow()
        total_success = 0

        async def kill_single(c: Container) -> bool:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, lambda: c.remove(force=True))
                return True
            except Exception:
                return False

        for i in range(0, len(containers), chunk_size):
            chunk = containers[i : i + chunk_size]
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(
                        *[kill_single(c) for c in chunk], return_exceptions=True
                    ),
                    timeout=30,
                )
                total_success += sum(1 for r in results if r is True)
            except asyncio.TimeoutError:
                logger.error(f"Batch kill timed out for chunk")

        duration = (datetime.utcnow() - start_time).total_seconds()
        logger.info(
            f"Batch kill completed: {total_success}/{len(containers)} in {duration:.2f}s"
        )
        return total_success

    async def cleanup_session_containers(self, session_id: str) -> int:
        """Clean up all containers for a session."""
        if not self.is_available():
            return 0

        try:
            containers = self.client.containers.list(
                all=True,
                filters={"label": f"com.code-interpreter.session-id={session_id}"},
            )

            if not containers:
                return 0

            return await self.force_kill_containers_batch(containers)
        except DockerException as e:
            logger.error(f"Failed to list containers for cleanup: {e}")
            return 0

    async def cleanup_all_code_execution_containers(
        self, max_age_minutes: int = None
    ) -> int:
        """Clean up old code execution containers."""
        if not self.is_available():
            return 0

        if max_age_minutes is None:
            max_age_minutes = settings.get_container_ttl_minutes()

        try:
            all_containers = self.client.containers.list(all=True)
            code_exec_containers = [
                c
                for c in all_containers
                if c.name.startswith("ci-exec-")
                or (c.labels and c.labels.get("com.code-interpreter.managed") == "true")
            ]

            if not code_exec_containers:
                return 0

            aged_containers = []
            for container in code_exec_containers:
                age = self._get_container_age(container)
                if age is not None and age > max_age_minutes:
                    aged_containers.append(container)

            if not aged_containers:
                return 0

            return await self.force_kill_containers_batch(aged_containers)
        except DockerException as e:
            logger.error(f"Failed to cleanup containers: {e}")
            return 0

    def _get_container_age(self, container) -> Optional[float]:
        """Get container age in minutes."""
        try:
            created_at_str = (
                container.labels.get("com.code-interpreter.created-at")
                if container.labels
                else None
            )
            if created_at_str:
                created_at = datetime.fromisoformat(created_at_str)
                age = datetime.utcnow() - created_at
                return age.total_seconds() / 60

            container.reload()
            created_str = container.attrs.get("Created")
            if created_str:
                import dateutil.parser

                created_at = dateutil.parser.parse(created_str).replace(tzinfo=None)
                age = datetime.utcnow() - created_at
                return age.total_seconds() / 60

            return None
        except Exception as e:
            logger.error(f"Failed to get container age: {e}")
            return None

    def close(self):
        """Close Docker client connection."""
        self._client_factory.close()
