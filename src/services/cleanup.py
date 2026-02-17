"""Event-driven cleanup scheduler with state archival.

This module provides event-driven cleanup that schedules container and resource
cleanup shortly after execution completes, rather than relying solely on polling.

Note: With the simplified pool (no session tracking), containers are destroyed
immediately after execution by the orchestrator. This scheduler handles:
- File cleanup when sessions are explicitly deleted
- Legacy cleanup for non-pooled containers
- Periodic state archival from Redis to MinIO
"""

import asyncio
from typing import Dict, Set, Optional

import structlog

from ..core.events import event_bus, ExecutionCompleted, SessionDeleted
from ..config import settings

logger = structlog.get_logger(__name__)


class CleanupScheduler:
    """Schedules cleanup operations after execution events.

    With the simplified container pool architecture:
    - Containers are destroyed immediately after execution (no TTL tracking)
    - This scheduler handles file cleanup and session-level resource cleanup
    - Periodic state archival from Redis to MinIO
    """

    def __init__(self, delay_seconds: int = 5):
        """Initialize cleanup scheduler.

        Args:
            delay_seconds: How long to wait after execution before cleanup
        """
        self.delay_seconds = delay_seconds
        self._pending_cleanups: Dict[str, asyncio.Task] = {}
        self._cleaned_sessions: Set[str] = set()
        self._execution_service = None
        self._file_service = None
        self._state_archival_service = None
        self._archival_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._started = False

    def set_services(
        self, execution_service, file_service, state_archival_service=None
    ):
        """Set service references for cleanup operations."""
        self._execution_service = execution_service
        self._file_service = file_service
        self._state_archival_service = state_archival_service

    def set_sandbox_pool(self, pool):
        """Set sandbox pool reference.

        Note: With simplified pool, sandboxes are destroyed immediately
        after execution. Pool reference is no longer used for cleanup.
        """
        logger.debug(
            "Cleanup scheduler initialized (sandboxes destroyed after each execution)"
        )

    def start(self):
        """Start listening for events and archival task."""
        if self._started:
            return

        # Register event handlers
        event_bus.register_handler(ExecutionCompleted, self._on_execution_completed)
        event_bus.register_handler(SessionDeleted, self._on_session_deleted)

        # Start archival background task if enabled
        if settings.state_archive_enabled and self._state_archival_service:
            self._archival_task = asyncio.create_task(self._archival_loop())
            logger.info(
                "State archival task started",
                interval_seconds=settings.state_archive_check_interval_seconds,
            )

        self._started = True
        logger.info("Cleanup scheduler started", delay_seconds=self.delay_seconds)

    def stop(self):
        """Stop the scheduler and cancel pending cleanups."""
        if not self._started:
            return

        # Unregister handlers
        event_bus.unregister_handler(ExecutionCompleted, self._on_execution_completed)
        event_bus.unregister_handler(SessionDeleted, self._on_session_deleted)

        # Cancel archival task
        if self._archival_task:
            self._archival_task.cancel()
            self._archival_task = None

        # Cancel pending cleanups
        for session_id, task in self._pending_cleanups.items():
            task.cancel()
        self._pending_cleanups.clear()
        self._cleaned_sessions.clear()
        self._started = False
        logger.info("Cleanup scheduler stopped")

    async def _on_execution_completed(self, event: ExecutionCompleted):
        """Handle execution completed event.

        With simplified pool, containers are destroyed immediately by orchestrator.
        This handler just logs the event for metrics purposes.
        """
        session_id = event.session_id

        # Containers are now destroyed immediately after execution
        # No deferred cleanup needed for containers
        logger.debug(
            "Execution completed (container already destroyed)",
            session_id=session_id[:12] if session_id else None,
            execution_id=event.execution_id[:8] if event.execution_id else None,
        )

    async def _on_session_deleted(self, event: SessionDeleted):
        """Handle session deleted event - cleanup file resources."""
        session_id = event.session_id

        async with self._lock:
            # Cancel pending cleanup if any
            if session_id in self._pending_cleanups:
                self._pending_cleanups[session_id].cancel()
                del self._pending_cleanups[session_id]

            # Mark as cleaned
            self._cleaned_sessions.add(session_id)

            # Limit set size
            if len(self._cleaned_sessions) > 1000:
                self._cleaned_sessions.clear()

        # Cleanup files for deleted session
        if self._file_service:
            try:
                await self._file_service.cleanup_session_files(session_id)
                logger.debug(
                    "Cleaned up files for deleted session",
                    session_id=session_id[:12] if session_id else None,
                )
            except Exception as e:
                logger.warning(
                    "Failed to cleanup session files",
                    session_id=session_id[:12] if session_id else None,
                    error=str(e),
                )

    def schedule_cleanup(self, session_id: str, delay_seconds: int = None):
        """Manually schedule cleanup for a session.

        Args:
            session_id: Session to clean up
            delay_seconds: Optional override for delay
        """
        if delay_seconds is None:
            delay_seconds = self.delay_seconds

        async def do_schedule():
            event = ExecutionCompleted(
                execution_id="manual", session_id=session_id, success=True
            )
            await self._on_execution_completed(event)

        asyncio.create_task(do_schedule())

    @property
    def pending_count(self) -> int:
        """Number of pending cleanup operations."""
        return len(self._pending_cleanups)

    async def _archival_loop(self):
        """Background loop for archiving inactive states to MinIO."""
        interval = settings.state_archive_check_interval_seconds

        while True:
            try:
                await asyncio.sleep(interval)

                if not self._state_archival_service:
                    continue

                # Archive inactive states
                result = await self._state_archival_service.archive_inactive_states()
                if result.get("archived", 0) > 0 or result.get("failed", 0) > 0:
                    logger.info(
                        "State archival cycle completed",
                        archived=result.get("archived", 0),
                        failed=result.get("failed", 0),
                    )

                # Cleanup expired archives periodically (less frequent)
                # Only run cleanup every 6 intervals
                if hasattr(self, "_archival_cleanup_counter"):
                    self._archival_cleanup_counter += 1
                else:
                    self._archival_cleanup_counter = 0

                if self._archival_cleanup_counter >= 6:
                    self._archival_cleanup_counter = 0
                    cleanup_result = (
                        await self._state_archival_service.cleanup_expired_archives()
                    )
                    if cleanup_result.get("deleted", 0) > 0:
                        logger.info(
                            "Expired archive cleanup completed",
                            deleted=cleanup_result.get("deleted", 0),
                        )

            except asyncio.CancelledError:
                logger.debug("State archival loop cancelled")
                break
            except Exception as e:
                logger.error("Error in state archival loop", error=str(e))
                await asyncio.sleep(60)  # Wait before retrying


# Global cleanup scheduler instance
cleanup_scheduler = CleanupScheduler(delay_seconds=5)
