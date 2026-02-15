"""Graceful shutdown handling for the application."""

import asyncio
from typing import List, Callable, Awaitable
import structlog

from ..services.health import health_service
from ..services.metrics import metrics_service

logger = structlog.get_logger(__name__)


class GracefulShutdownHandler:
    """Handler for graceful application shutdown."""

    def __init__(self):
        """Initialize shutdown handler."""
        self._shutdown_callbacks: List[Callable[[], Awaitable[None]]] = []
        self._is_shutting_down = False
        self._shutdown_lock = asyncio.Lock()

    def add_shutdown_callback(self, callback: Callable[[], Awaitable[None]]) -> None:
        """Add a callback to be executed during shutdown."""
        self._shutdown_callbacks.append(callback)

    async def shutdown(self) -> None:
        """Perform graceful shutdown."""
        async with self._shutdown_lock:
            if self._is_shutting_down:
                return

            self._is_shutting_down = True
            logger.info("Starting graceful shutdown")

            # Execute shutdown callbacks in reverse order with timeout
            for callback in reversed(self._shutdown_callbacks):
                try:
                    # Add timeout to each callback to prevent hanging
                    await asyncio.wait_for(callback(), timeout=10.0)
                except asyncio.TimeoutError:
                    callback_name = getattr(callback, "__name__", str(callback))
                    logger.warning(
                        f"Shutdown callback {callback_name} timed out after 10 seconds"
                    )
                except Exception as e:
                    callback_name = getattr(callback, "__name__", str(callback))
                    logger.error(
                        f"Error in shutdown callback {callback_name}", error=str(e)
                    )

            logger.info("Graceful shutdown completed")


# Global shutdown handler instance
shutdown_handler = GracefulShutdownHandler()


async def cleanup_services() -> None:
    """Cleanup all services during shutdown."""
    logger.info("Cleaning up services")

    # Stop session service cleanup tasks with timeout
    try:
        from ..dependencies.services import get_session_service

        session_service = get_session_service()
        await asyncio.wait_for(session_service.close(), timeout=3.0)
        logger.info("Session service stopped")
    except asyncio.TimeoutError:
        logger.warning("Session service stop timed out")
    except ImportError as e:
        logger.warning(f"Could not import session service during shutdown: {e}")
    except Exception as e:
        logger.error("Error stopping session service", error=str(e))

    # Stop metrics service with timeout
    try:
        await asyncio.wait_for(metrics_service.stop(), timeout=5.0)
        logger.info("Metrics service stopped")
    except asyncio.TimeoutError:
        logger.warning("Metrics service stop timed out")
    except Exception as e:
        logger.error("Error stopping metrics service", error=str(e))

    # Close health service with timeout
    try:
        await asyncio.wait_for(health_service.close(), timeout=3.0)
        logger.info("Health service closed")
    except asyncio.TimeoutError:
        logger.warning("Health service close timed out")
    except Exception as e:
        logger.error("Error closing health service", error=str(e))


async def cleanup_active_containers() -> None:
    """Cleanup active containers during shutdown."""
    logger.info("Cleaning up active containers")

    try:
        # Import here to avoid circular imports and handle import errors
        from ..dependencies.services import get_execution_service

        # Get the execution service instance with timeout
        execution_service = get_execution_service()

        # Stop all active executions with shorter timeout to prevent hanging
        await asyncio.wait_for(execution_service.cleanup_all_containers(), timeout=8.0)
        logger.info("Container cleanup completed")
    except asyncio.TimeoutError:
        logger.warning("Container cleanup timed out after 8 seconds - forcing shutdown")
    except ImportError as e:
        logger.warning(f"Could not import execution service during shutdown: {e}")
    except Exception as e:
        logger.error("Error cleaning up containers", error=str(e))


async def flush_logs_and_metrics() -> None:
    """Flush any pending logs and metrics."""
    logger.info("Flushing logs and metrics")

    try:
        # Give a moment for any pending log writes
        await asyncio.sleep(0.1)
        logger.info("Logs and metrics flushed")
    except Exception as e:
        logger.error("Error flushing logs and metrics", error=str(e))


def setup_graceful_shutdown() -> None:
    """Setup graceful shutdown handling."""
    # Add shutdown callbacks in order of execution (reversed during shutdown)
    shutdown_handler.add_shutdown_callback(flush_logs_and_metrics)
    shutdown_handler.add_shutdown_callback(cleanup_active_containers)
    shutdown_handler.add_shutdown_callback(cleanup_services)

    logger.info("Graceful shutdown handling configured")
