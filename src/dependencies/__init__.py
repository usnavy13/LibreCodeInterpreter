"""Dependencies package for the Code Interpreter API.

Supports multiple deployment modes:
- Docker mode (default): Uses Docker containers, MinIO, local Redis
- Azure mode: Uses HTTP executor, Azure Blob Storage, Azure Cache for Redis
- Unified mode: Single container with inline execution (Azure + UNIFIED_MODE=true)

Set DEPLOYMENT_MODE=azure for Azure deployment.
Set UNIFIED_MODE=true for unified single-container deployment.
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


def is_unified_mode() -> bool:
    """Check if running in unified single-container mode."""
    return os.environ.get("UNIFIED_MODE", "false").lower() == "true"


# Import the appropriate services module based on deployment mode
if is_azure_deployment() and is_unified_mode():
    # Unified mode: single container with inline execution
    from .services_unified import (
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
elif is_azure_deployment():
    # Azure mode with separate executor container
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
    # Docker mode (default)
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
    "is_unified_mode",
    "FileServiceDep",
    "SessionServiceDep",
    "ExecutionServiceDep",
    "StateServiceDep",
    "StateArchivalServiceDep",
]
