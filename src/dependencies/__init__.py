"""Dependencies package for the Code Interpreter API.

Supports both Docker and Azure deployment modes:
- Docker mode (default): Uses Docker containers, MinIO, local Redis
- Azure mode: Uses HTTP executor, Azure Blob Storage, Azure Cache for Redis

Set DEPLOYMENT_MODE=azure to use Azure services.
"""

import os

from .auth import (
    verify_api_key,
    verify_api_key_optional,
    get_current_user,
    get_current_user_optional,
    AuthenticatedUser,
)


def is_azure_deployment() -> bool:
    """Check if running in Azure deployment mode."""
    return os.environ.get("DEPLOYMENT_MODE", "docker").lower() == "azure"


# Import the appropriate services module based on deployment mode
if is_azure_deployment():
    from .services_azure import (
        get_file_service,
        get_session_service,
        get_state_service,
        get_state_archival_service,
        get_execution_service,
        set_container_pool,
        get_container_pool,
        inject_container_pool_to_execution_service,
        FileServiceDep,
        SessionServiceDep,
        ExecutionServiceDep,
        StateServiceDep,
        StateArchivalServiceDep,
    )
else:
    from .services import (
        get_file_service,
        get_session_service,
        get_state_service,
        get_state_archival_service,
        get_execution_service,
        set_container_pool,
        get_container_pool,
        inject_container_pool_to_execution_service,
        FileServiceDep,
        SessionServiceDep,
        ExecutionServiceDep,
        StateServiceDep,
        StateArchivalServiceDep,
    )


__all__ = [
    "verify_api_key",
    "verify_api_key_optional",
    "get_current_user",
    "get_current_user_optional",
    "AuthenticatedUser",
    "get_file_service",
    "get_session_service",
    "get_state_service",
    "get_state_archival_service",
    "get_execution_service",
    "set_container_pool",
    "get_container_pool",
    "inject_container_pool_to_execution_service",
    "is_azure_deployment",
    "FileServiceDep",
    "SessionServiceDep",
    "ExecutionServiceDep",
    "StateServiceDep",
    "StateArchivalServiceDep",
]
