"""Service dependency injection for Azure Container Apps deployment.

This module provides service factories that use Azure services:
- Azure Blob Storage instead of MinIO
- Azure Cache for Redis
- HTTP-based executor service instead of Docker

Use this module when DEPLOYMENT_MODE=azure.
"""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends
import structlog

from ..config import settings
from ..config.azure import azure_settings
from ..services.file_azure import AzureFileService
from ..services.state import StateService
from ..services.state_archival_azure import AzureStateArchivalService
from ..services.azure_execution import AzureExecutionService
from ..services import SessionService
from ..services.interfaces import (
    FileServiceInterface,
    SessionServiceInterface,
    ExecutionServiceInterface,
)

logger = structlog.get_logger(__name__)


@lru_cache()
def get_file_service() -> FileServiceInterface:
    """Get Azure file service instance."""
    return AzureFileService(
        connection_string=azure_settings.azure_storage_connection_string,
        container_name=azure_settings.azure_storage_container,
        redis_url=azure_settings.get_redis_url() or settings.get_redis_url(),
    )


@lru_cache()
def get_state_service() -> StateService:
    """Get state service instance with Azure Redis connection."""
    import redis as redis_lib

    redis_url = azure_settings.get_redis_url() or settings.get_redis_url()
    if redis_url:
        redis_client = redis_lib.from_url(redis_url, decode_responses=False)
        return StateService(redis_client=redis_client)
    return StateService()


@lru_cache()
def get_state_archival_service() -> AzureStateArchivalService:
    """Get Azure state archival service instance."""
    state_service = get_state_service()
    return AzureStateArchivalService(
        state_service=state_service,
        connection_string=azure_settings.azure_storage_connection_string,
        container_name=azure_settings.azure_storage_container,
    )


@lru_cache()
def get_execution_service() -> ExecutionServiceInterface:
    """Get Azure execution service instance (HTTP-based)."""
    return AzureExecutionService(
        executor_url=azure_settings.executor_url,
        timeout=azure_settings.executor_timeout,
        max_retries=azure_settings.executor_max_retries,
    )


@lru_cache()
def get_session_service() -> SessionServiceInterface:
    """Get session service instance with Azure dependencies."""
    import redis as redis_lib

    try:
        # Get Redis client for Azure
        redis_url = azure_settings.get_redis_url() or settings.get_redis_url()
        redis_client = None
        if redis_url:
            redis_client = redis_lib.asyncio.from_url(redis_url, decode_responses=False)

        # Wire up dependencies
        execution_service = get_execution_service()
        file_service = get_file_service()

        session_service = SessionService(
            redis_client=redis_client,
            execution_service=execution_service,
            file_service=file_service,
        )

        logger.info("Azure session service initialized with dependencies")
        return session_service

    except Exception as e:
        logger.error("Failed to initialize session service", error=str(e))
        return SessionService()


# Type aliases for dependency injection (same interface as Docker mode)
FileServiceDep = Annotated[FileServiceInterface, Depends(get_file_service)]
SessionServiceDep = Annotated[SessionServiceInterface, Depends(get_session_service)]
ExecutionServiceDep = Annotated[
    ExecutionServiceInterface, Depends(get_execution_service)
]
StateServiceDep = Annotated[StateService, Depends(get_state_service)]
StateArchivalServiceDep = Annotated[
    AzureStateArchivalService, Depends(get_state_archival_service)
]


# No container pool in Azure mode
def set_container_pool(pool) -> None:
    """No-op in Azure mode - we don't use Docker containers."""
    logger.debug("Container pool not used in Azure deployment mode")


def get_container_pool():
    """No container pool in Azure mode."""
    return None


def inject_container_pool_to_execution_service():
    """No-op in Azure mode - executor service uses HTTP."""
    pass
