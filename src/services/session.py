"""Redis-based session management service implementation."""

# Standard library imports
import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict

# Third-party imports
import redis.asyncio as redis
import structlog

# Local application imports
from .interfaces import SessionServiceInterface
from ..config import settings
from ..core.pool import redis_pool
from ..models.session import Session, SessionCreate, SessionStatus
from ..utils.id_generator import generate_session_id

logger = structlog.get_logger(__name__)


class SessionService(SessionServiceInterface):
    """Redis-based session management service."""

    def __init__(
        self,
        redis_client: Optional[redis.Redis] = None,
        execution_service=None,
        file_service=None,
    ):
        """Initialize the session service with Redis client."""
        self.redis = redis_client or redis_pool.get_client()
        self._cleanup_task: Optional[asyncio.Task] = None
        self._execution_service = execution_service
        self._file_service = file_service
        self._redis_available = False

    def set_execution_service(self, execution_service) -> None:
        """Set the execution service dependency."""
        self._execution_service = execution_service

    def set_file_service(self, file_service) -> None:
        """Set the file service dependency."""
        self._file_service = file_service
        logger.info("Redis client created", url=settings.get_redis_url().split("@")[-1])

    async def _check_redis_connectivity(self) -> bool:
        """Check if Redis is available and working."""
        try:
            await self.redis.ping()
            self._redis_available = True
            logger.info("Redis connectivity confirmed")
            return True
        except Exception as e:
            self._redis_available = False
            logger.error("Redis connectivity failed", error=str(e))
            return False

    async def start_cleanup_task(self) -> None:
        """Start the background cleanup task."""
        # First check Redis connectivity
        if not await self._check_redis_connectivity():
            logger.warning("Cannot start session cleanup task - Redis not available")
            return

        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info(
                "Session cleanup task started",
                ttl_hours=settings.session_ttl_hours,
                cleanup_interval_minutes=settings.session_cleanup_interval_minutes,
            )

    async def stop_cleanup_task(self) -> None:
        """Stop the background cleanup task."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            logger.info("Session cleanup task stopped")

    async def _cleanup_loop(self) -> None:
        """Background task to clean up expired sessions."""
        while True:
            try:
                # Re-check Redis connectivity periodically
                if not self._redis_available:
                    if not await self._check_redis_connectivity():
                        logger.warning("Session cleanup skipped - Redis not available")
                        await asyncio.sleep(60)  # Wait shorter time when Redis is down
                        continue

                cleaned_count = await self.cleanup_expired_sessions()
                if cleaned_count > 0:
                    logger.info("Cleaned up expired sessions", count=cleaned_count)
                else:
                    logger.debug("No expired sessions to clean up")

                # Opportunistically prune orphan MinIO objects (configurable)
                if self._file_service and settings.enable_orphan_minio_cleanup:
                    try:
                        deleted_orphans = (
                            await self._file_service.cleanup_orphan_objects()
                        )
                        if deleted_orphans:
                            logger.info(
                                "Pruned orphan MinIO objects",
                                deleted_orphans=deleted_orphans,
                            )
                    except Exception as e:
                        logger.error(
                            "Failed pruning orphan MinIO objects", error=str(e)
                        )

                # Wait for the configured cleanup interval
                await asyncio.sleep(settings.session_cleanup_interval_minutes * 60)

            except asyncio.CancelledError:
                logger.info("Session cleanup task cancelled")
                break
            except Exception as e:
                logger.error("Error in session cleanup task", error=str(e))
                self._redis_available = False  # Mark Redis as potentially unavailable
                # Wait a bit before retrying
                await asyncio.sleep(60)

    def _generate_session_id(self) -> str:
        """Generate a unique session ID."""
        return generate_session_id()

    def _session_key(self, session_id: str) -> str:
        """Generate Redis key for session data."""
        return f"sessions:{session_id}"

    def _session_index_key(self) -> str:
        """Generate Redis key for session index."""
        return "sessions:index"

    def _entity_sessions_key(self, entity_id: str) -> str:
        """Generate Redis key for entity-based session grouping."""
        return f"entity_sessions:{entity_id}"

    async def create_session(self, request: SessionCreate) -> Session:
        """Create a new code execution session."""
        session_id = self._generate_session_id()
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=settings.get_session_ttl_minutes())

        # Ensure metadata is not None
        metadata = request.metadata if request.metadata is not None else {}

        session = Session(
            session_id=session_id,
            status=SessionStatus.ACTIVE,
            created_at=now,
            last_activity=now,
            expires_at=expires_at,
            metadata=metadata,
        )

        # Store session data in Redis
        session_key = self._session_key(session_id)
        session_data = session.model_dump()

        # Convert datetime objects to ISO strings for Redis storage
        # Also handle None values and other types that Redis can't store
        for key, value in session_data.items():
            if isinstance(value, datetime):
                session_data[key] = value.isoformat()
            elif value is None:
                session_data[key] = ""
            elif isinstance(value, (dict, list)):
                session_data[key] = json.dumps(value)
            elif not isinstance(value, (str, int, float)):
                session_data[key] = str(value)

        # Extract entity_id from metadata if provided
        entity_id = request.metadata.get("entity_id") if request.metadata else None

        # Use Redis transaction to ensure atomicity
        pipe = await self.redis.pipeline(transaction=True)
        try:
            # Store session data
            pipe.hset(session_key, mapping=session_data)
            # Set expiration
            pipe.expire(session_key, int(settings.get_session_ttl_minutes() * 60))
            # Add to session index
            pipe.sadd(self._session_index_key(), session_id)

            # Add to entity-based grouping if entity_id is provided
            if entity_id:
                pipe.sadd(self._entity_sessions_key(entity_id), session_id)

            await pipe.execute()
        except Exception as e:
            logger.error(
                "Redis pipeline execution failed", session_id=session_id, error=str(e)
            )
            raise
        finally:
            await pipe.reset()

        logger.info(
            "Session created", session_id=session_id, expires_at=expires_at.isoformat()
        )
        return session

    async def get_session(self, session_id: str) -> Optional[Session]:
        """Retrieve a session by ID."""
        session_key = self._session_key(session_id)
        session_data = await self.redis.hgetall(session_key)

        if not session_data:
            return None

        # Convert ISO strings back to datetime objects
        for key in ["created_at", "last_activity", "expires_at"]:
            if key in session_data and session_data[key]:
                session_data[key] = datetime.fromisoformat(session_data[key])

        # Parse JSON fields
        if "files" in session_data and session_data["files"]:
            session_data["files"] = json.loads(session_data["files"])
        else:
            session_data["files"] = {}

        if "metadata" in session_data and session_data["metadata"]:
            session_data["metadata"] = json.loads(session_data["metadata"])
        else:
            session_data["metadata"] = {}

        # Convert numeric fields (handle empty strings as None)
        for key in ["memory_usage_mb", "cpu_usage_percent"]:
            if key in session_data:
                if session_data[key] and session_data[key] != "":
                    try:
                        session_data[key] = float(session_data[key])
                    except (ValueError, TypeError):
                        session_data[key] = None
                else:
                    session_data[key] = None

        try:
            session = Session(**session_data)

            # Update last activity if session is active
            if session.status == SessionStatus.ACTIVE:
                await self._update_last_activity(session_id)

            return session
        except Exception as e:
            logger.error(
                "Error parsing session data", session_id=session_id, error=str(e)
            )
            return None

    async def update_session(self, session_id: str, **updates) -> Optional[Session]:
        """Update session properties."""
        session_key = self._session_key(session_id)

        # Check if session exists
        if not await self.redis.exists(session_key):
            return None

        # Prepare updates for Redis
        redis_updates = {}
        for key, value in updates.items():
            if isinstance(value, datetime):
                redis_updates[key] = value.isoformat()
            elif isinstance(value, (dict, list)):
                redis_updates[key] = json.dumps(value)
            else:
                redis_updates[key] = str(value)

        # Update last activity
        redis_updates["last_activity"] = datetime.now(timezone.utc).isoformat()

        # Apply updates
        await self.redis.hset(session_key, mapping=redis_updates)

        # Return updated session
        return await self.get_session(session_id)

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session and cleanup resources."""
        session_key = self._session_key(session_id)

        # Get session data to check for entity_id before deletion
        session = await self.get_session(session_id)
        entity_id = None
        if session and session.metadata:
            entity_id = session.metadata.get("entity_id")

        # Clean up execution resources (containers) BEFORE deleting session
        if self._execution_service:
            try:
                await self._execution_service.cleanup_session(session_id)
                logger.info(
                    "Cleaned up execution resources for session", session_id=session_id
                )
            except Exception as e:
                logger.error(
                    "Failed to cleanup execution resources for session",
                    session_id=session_id,
                    error=str(e),
                )
                # Continue with session deletion even if container cleanup fails

        # Clean up file resources BEFORE deleting session
        if self._file_service:
            try:
                deleted_files = await self._file_service.cleanup_session_files(
                    session_id
                )
                logger.info(
                    "Cleaned up file resources for session",
                    session_id=session_id,
                    deleted_files=deleted_files,
                )
            except Exception as e:
                logger.error(
                    "Failed to cleanup file resources for session",
                    session_id=session_id,
                    error=str(e),
                )
                # Continue with session deletion even if file cleanup fails

        # Use transaction to ensure atomicity
        pipe = await self.redis.pipeline(transaction=True)
        try:
            # Remove session data
            pipe.delete(session_key)
            # Remove from session index
            pipe.srem(self._session_index_key(), session_id)

            # Remove from entity-based grouping if entity_id exists
            if entity_id:
                pipe.srem(self._entity_sessions_key(entity_id), session_id)

            result = await pipe.execute()
        finally:
            await pipe.reset()

        deleted = result[0] > 0  # First command result (delete)

        if deleted:
            logger.info("Session deleted", session_id=session_id, entity_id=entity_id)

        return deleted

    async def list_sessions(self, limit: int = 100, offset: int = 0) -> List[Session]:
        """List all active sessions."""
        # Get all session IDs from the index
        session_ids = await self.redis.smembers(self._session_index_key())

        # Apply pagination
        session_ids = list(session_ids)[offset : offset + limit]

        sessions = []
        for session_id in session_ids:
            session = await self.get_session(session_id)
            if session:
                sessions.append(session)

        return sessions

    async def cleanup_expired_sessions(self) -> int:
        """Clean up expired sessions and return count of cleaned sessions."""
        now = datetime.now(timezone.utc)
        cleaned_count = 0

        # Get all session IDs
        session_ids = await self.redis.smembers(self._session_index_key())

        for session_id in session_ids:
            session = await self.get_session(session_id)
            # If session data is missing, treat as expired/orphaned and clean up indexes
            if not session:
                logger.info(
                    "Cleaning up orphaned session (missing data)", session_id=session_id
                )
                # Attempt to clean up any files associated with this session by prefix
                if self._file_service:
                    try:
                        deleted_files = await self._file_service.cleanup_session_files(
                            session_id
                        )
                        logger.info(
                            "Cleaned up files for orphaned session",
                            session_id=session_id,
                            deleted_files=deleted_files,
                        )
                    except Exception as e:
                        logger.error(
                            "Failed to cleanup files for orphaned session",
                            session_id=session_id,
                            error=str(e),
                        )
                # Remove from session index regardless
                try:
                    await self.redis.srem(self._session_index_key(), session_id)
                except Exception as e:
                    logger.error(
                        "Failed to remove orphaned session from index",
                        session_id=session_id,
                        error=str(e),
                    )
                cleaned_count += 1
                continue

            if session.expires_at < now:
                logger.info(
                    "Cleaning up expired session",
                    session_id=session_id,
                    expired_at=session.expires_at.isoformat(),
                    current_time=now.isoformat(),
                )
                await self.delete_session(session_id)
                cleaned_count += 1

        if cleaned_count > 0:
            logger.info(
                "Completed expired session cleanup",
                cleaned_sessions=cleaned_count,
                remaining_sessions=len(session_ids) - cleaned_count,
            )

        return cleaned_count

    async def force_cleanup_all_sessions(self) -> int:
        """Force cleanup of all sessions (for testing/emergency use)."""
        session_ids = await self.redis.smembers(self._session_index_key())
        cleaned_count = 0

        for session_id in session_ids:
            await self.delete_session(session_id)
            cleaned_count += 1

        logger.info("Force cleaned all sessions", cleaned_count=cleaned_count)
        return cleaned_count

    async def list_sessions_by_entity(
        self, entity_id: str, limit: int = 100, offset: int = 0
    ) -> List[Session]:
        """List sessions associated with a specific entity."""
        # Get session IDs for the entity
        session_ids = await self.redis.smembers(self._entity_sessions_key(entity_id))

        # Apply pagination
        session_ids = list(session_ids)[offset : offset + limit]

        sessions = []
        for session_id in session_ids:
            session = await self.get_session(session_id)
            if session:
                sessions.append(session)

        return sessions

    async def validate_session_access(
        self, session_id: str, entity_id: Optional[str] = None
    ) -> bool:
        """Validate if a session can be accessed, optionally checking entity association."""
        session = await self.get_session(session_id)
        if not session:
            return False

        # If entity_id is provided, check if session belongs to that entity
        if entity_id:
            session_entity_id = (
                session.metadata.get("entity_id") if session.metadata else None
            )
            if session_entity_id != entity_id:
                return False

        return True

    async def close(self) -> None:
        """Close the session service and stop cleanup tasks."""
        # Stop cleanup task
        await self.stop_cleanup_task()

        # Close Redis connection
        if self.redis:
            try:
                await self.redis.close()
                logger.info("Session service closed successfully")
            except Exception as e:
                logger.error("Error closing session service", error=str(e))

    async def get_session_files_access(
        self, session_id: str, entity_id: Optional[str] = None
    ) -> bool:
        """Check if files in a session can be accessed based on entity grouping."""
        # First validate basic session access
        if not await self.validate_session_access(session_id, entity_id):
            return False

        # If entity_id is provided, also check if there are other sessions
        # in the same entity that might have shared files
        if entity_id:
            entity_sessions = await self.list_sessions_by_entity(entity_id)
            # If the session belongs to the entity, allow access
            return any(s.session_id == session_id for s in entity_sessions)

        return True

    async def _update_last_activity(self, session_id: str) -> None:
        """Update the last activity timestamp for a session."""
        session_key = self._session_key(session_id)
        await self.redis.hset(
            session_key, "last_activity", datetime.now(timezone.utc).isoformat()
        )
