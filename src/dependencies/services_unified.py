"""Service dependency injection for Unified Azure Container Apps deployment.

This module provides service factories for the unified single-image deployment:
- Azure Blob Storage for files
- Azure Cache for Redis for sessions/state
- Inline executor (in-process, no HTTP calls)

Use this module when DEPLOYMENT_MODE=azure and UNIFIED_MODE=true.
"""

import os
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
import structlog

from ..config import settings
from ..config.azure import azure_settings
from ..services.file_azure import AzureFileService
from ..services.state import StateService
from ..services.state_archival_azure import AzureStateArchivalService
from ..services.inline_executor import InlineExecutionService
from ..services import SessionService
from ..services.interfaces import (
    FileServiceInterface,
    SessionServiceInterface,
    ExecutionServiceInterface,
)

logger = structlog.get_logger(__name__)


def is_unified_mode() -> bool:
    """Check if running in unified mode."""
    return os.environ.get("UNIFIED_MODE", "false").lower() == "true"


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
    import redis.asyncio as redis_async

    redis_url = azure_settings.get_redis_url() or settings.get_redis_url()
    if redis_url:
        # StateService uses async redis and stores binary state data
        redis_client = redis_async.from_url(redis_url, decode_responses=False)
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
    """Get inline execution service instance (in-process, serialized)."""
    # Get file service for downloading file content
    file_service = get_file_service()

    # Get max concurrent from environment (default 1 for serialized execution)
    max_concurrent = int(os.environ.get("MAX_CONCURRENT_EXECUTIONS", "1"))
    working_dir = os.environ.get("WORKING_DIR_BASE", "/mnt/data")

    service = InlineExecutionService(
        max_concurrent=max_concurrent,
        working_dir=working_dir,
        file_service=file_service,
    )

    logger.info(
        "Created inline execution service",
        max_concurrent=max_concurrent,
        working_dir=working_dir,
    )

    return service


@lru_cache()
def get_session_service() -> SessionServiceInterface:
    """Get session service instance with Azure dependencies."""
    import redis as redis_lib

    try:
        # Get Redis client for Azure
        redis_url = azure_settings.get_redis_url() or settings.get_redis_url()
        redis_client = None
        if redis_url:
            redis_client = redis_lib.asyncio.from_url(redis_url, decode_responses=True)

        # Wire up dependencies
        execution_service = get_execution_service()
        file_service = get_file_service()

        session_service = SessionService(
            redis_client=redis_client,
            execution_service=execution_service,
            file_service=file_service,
        )

        logger.info("Unified session service initialized with dependencies")
        return session_service

    except Exception as e:
        logger.error("Failed to initialize session service", error=str(e))
        return SessionService()


# Type aliases for dependency injection (same interface as other modes)
FileServiceDep = Annotated[FileServiceInterface, Depends(get_file_service)]
SessionServiceDep = Annotated[SessionServiceInterface, Depends(get_session_service)]
ExecutionServiceDep = Annotated[
    ExecutionServiceInterface, Depends(get_execution_service)
]
StateServiceDep = Annotated[StateService, Depends(get_state_service)]
StateArchivalServiceDep = Annotated[
    AzureStateArchivalService, Depends(get_state_archival_service)
]


# No container pool in unified mode
def set_container_pool(pool) -> None:
    """No-op in unified mode - we use inline execution."""
    logger.debug("Container pool not used in unified deployment mode")


def get_container_pool():
    """No container pool in unified mode."""
    return None


def inject_container_pool_to_execution_service():
    """No-op in unified mode - executor service uses inline execution."""
    pass
