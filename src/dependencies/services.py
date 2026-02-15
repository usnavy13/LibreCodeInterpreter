"""Service dependency injection for the Code Interpreter API."""

# Standard library imports
from functools import lru_cache
from typing import Annotated

# Third-party imports
from fastapi import Depends
import structlog

# Local application imports
from ..services import FileService, SessionService, CodeExecutionService
from ..services.state import StateService
from ..services.state_archival import StateArchivalService
from ..services.interfaces import (
    FileServiceInterface,
    SessionServiceInterface,
    ExecutionServiceInterface,
)

logger = structlog.get_logger(__name__)

# Global reference to container pool (set by main.py lifespan)
_container_pool = None


def set_container_pool(pool) -> None:
    """Set the global container pool reference.

    Called by main.py after the pool is initialized in lifespan.
    """
    global _container_pool
    _container_pool = pool
    logger.info("Container pool registered with dependency injection")


def get_container_pool():
    """Get the container pool instance (may be None if disabled)."""
    return _container_pool


@lru_cache()
def get_file_service() -> FileServiceInterface:
    """Get file service instance."""
    return FileService()


@lru_cache()
def get_state_service() -> StateService:
    """Get state service instance for Python session state persistence."""
    return StateService()


@lru_cache()
def get_state_archival_service() -> StateArchivalService:
    """Get state archival service instance for MinIO cold storage."""
    state_service = get_state_service()
    return StateArchivalService(state_service=state_service)


@lru_cache()
def get_execution_service() -> ExecutionServiceInterface:
    """Get execution service instance.

    Note: Container pool is injected separately after creation via set_container_pool.
    """
    return CodeExecutionService()


def inject_container_pool_to_execution_service():
    """Inject container pool into the execution service.

    Called after pool is initialized to wire it into the cached execution service.
    """
    if _container_pool:
        execution_service = get_execution_service()
        execution_service.set_container_pool(_container_pool)
        logger.info("Container pool injected into execution service")


@lru_cache()
def get_session_service() -> SessionServiceInterface:
    """Get session service instance with proper dependency injection."""
    try:
        # Don't inject dependencies during initialization to avoid circular imports
        # The services will coordinate during runtime
        session_service = SessionService()

        # Set up service references after initialization
        execution_service = get_execution_service()
        file_service = get_file_service()

        # Wire up the dependencies
        session_service.set_execution_service(execution_service)
        session_service.set_file_service(file_service)

        logger.info("Session service initialized with dependencies")
        return session_service

    except Exception as e:
        logger.error("Failed to initialize session service", error=str(e))
        # Return basic session service without dependencies as fallback
        return SessionService()


# Type aliases for dependency injection
FileServiceDep = Annotated[FileServiceInterface, Depends(get_file_service)]
SessionServiceDep = Annotated[SessionServiceInterface, Depends(get_session_service)]
ExecutionServiceDep = Annotated[
    ExecutionServiceInterface, Depends(get_execution_service)
]
StateServiceDep = Annotated[StateService, Depends(get_state_service)]
StateArchivalServiceDep = Annotated[
    StateArchivalService, Depends(get_state_archival_service)
]
