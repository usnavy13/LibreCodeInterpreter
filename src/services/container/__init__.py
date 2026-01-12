"""Container management services.

This package provides Docker container management functionality split into:
- client.py: Docker client factory and initialization
- executor.py: Command execution in containers
- manager.py: Container lifecycle management
- utils.py: Shared utilities for container operations
"""

from .manager import ContainerManager
from .client import DockerClientFactory
from .executor import ContainerExecutor
from .utils import wait_for_container_ready, receive_socket_output, run_in_executor

__all__ = [
    "ContainerManager",
    "DockerClientFactory",
    "ContainerExecutor",
    "wait_for_container_ready",
    "receive_socket_output",
    "run_in_executor",
]
