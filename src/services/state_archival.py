"""State archival service for MinIO cold storage.

This service handles archiving Python session states from Redis to MinIO
for long-term storage, and restoring them on demand.

Hybrid storage architecture:
- Hot storage: Redis with 2-hour TTL (fast access)
- Cold storage: MinIO with 7-day TTL (long-term archival)

When a state is accessed:
1. Check Redis first (hot storage)
2. If not found, check MinIO (cold storage)
3. If found in MinIO, restore to Redis

States are archived to MinIO when:
- TTL in Redis drops below archive_after_seconds threshold
- This indicates the session has been inactive for a while
"""

import asyncio
import io
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

import structlog
from minio import Minio
from minio.error import S3Error

from ..config import settings
from .state import StateService

logger = structlog.get_logger(__name__)


class StateArchivalService:
    """Manages archiving and restoring Python session states to/from MinIO.

    States are stored in MinIO under the path:
        states/{session_id}/state.dat

    Metadata is stored as object tags/custom metadata:
        - archived_at: ISO timestamp
        - original_size: Size before any host-side compression
        - session_id: The session identifier
    """

    # MinIO path prefix for archived states
    STATE_PREFIX = "states"
    STATE_BY_HASH_PREFIX = "states/by_hash"  # For hash-indexed state storage

    def __init__(
        self,
        state_service: Optional[StateService] = None,
        minio_client: Optional[Minio] = None,
    ):
        """Initialize the archival service.

        Args:
            state_service: StateService instance for Redis operations
            minio_client: Optional MinIO client (creates new one if not provided)
        """
        self.state_service = state_service or StateService()
        self.minio_client = minio_client or Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        self.bucket_name = settings.minio_bucket
        self._bucket_checked = False

    def _get_state_object_key(self, session_id: str) -> str:
        """Generate MinIO object key for a session state."""
        return f"{self.STATE_PREFIX}/{session_id}/state.dat"

    async def _ensure_bucket_exists(self) -> None:
        """Ensure the MinIO bucket exists."""
        if self._bucket_checked:
            return

        try:
            loop = asyncio.get_event_loop()
            bucket_exists = await loop.run_in_executor(
                None, self.minio_client.bucket_exists, self.bucket_name
            )

            if not bucket_exists:
                await loop.run_in_executor(
                    None, self.minio_client.make_bucket, self.bucket_name
                )
                logger.info(
                    "Created MinIO bucket for state archival", bucket=self.bucket_name
                )

            self._bucket_checked = True

        except S3Error as e:
            logger.error(
                "Failed to ensure bucket exists", error=str(e), bucket=self.bucket_name
            )
            raise

    async def archive_state(self, session_id: str, state_data: str) -> bool:
        """Archive a session state to MinIO.

        Args:
            session_id: Session identifier
            state_data: Base64-encoded state data (already lz4 compressed)

        Returns:
            True if archived successfully
        """
        try:
            await self._ensure_bucket_exists()

            object_key = self._get_state_object_key(session_id)
            state_bytes = state_data.encode("utf-8")

            # Create metadata
            metadata = {
                "archived_at": datetime.now(timezone.utc).isoformat(),
                "original_size": str(len(state_bytes)),
                "session_id": session_id,
            }

            # Upload to MinIO
            loop = asyncio.get_event_loop()
            data_stream = io.BytesIO(state_bytes)

            await loop.run_in_executor(
                None,
                lambda: self.minio_client.put_object(
                    self.bucket_name,
                    object_key,
                    data_stream,
                    len(state_bytes),
                    content_type="application/octet-stream",
                    metadata=metadata,
                ),
            )

            logger.info(
                "Archived state to MinIO",
                session_id=session_id[:12],
                size_bytes=len(state_bytes),
                object_key=object_key,
            )
            return True

        except Exception as e:
            logger.error(
                "Failed to archive state", session_id=session_id[:12], error=str(e)
            )
            return False

    async def restore_state(self, session_id: str) -> Optional[str]:
        """Restore a session state from MinIO.

        If found, the state is also saved back to Redis for fast access.

        Args:
            session_id: Session identifier

        Returns:
            Base64-encoded state data, or None if not found
        """
        try:
            await self._ensure_bucket_exists()

            object_key = self._get_state_object_key(session_id)
            loop = asyncio.get_event_loop()

            # Check if object exists
            try:
                response = await loop.run_in_executor(
                    None,
                    lambda: self.minio_client.get_object(self.bucket_name, object_key),
                )
                state_bytes = response.read()
                response.close()
                response.release_conn()
            except S3Error as e:
                if e.code == "NoSuchKey":
                    logger.debug("No archived state found", session_id=session_id[:12])
                    return None
                raise

            state_data = state_bytes.decode("utf-8")

            # Restore to Redis for fast access
            await self.state_service.save_state(
                session_id, state_data, ttl_seconds=settings.state_ttl_seconds
            )

            logger.info(
                "Restored state from MinIO",
                session_id=session_id[:12],
                size_bytes=len(state_bytes),
            )
            return state_data

        except Exception as e:
            logger.error(
                "Failed to restore state", session_id=session_id[:12], error=str(e)
            )
            return None

    async def delete_archived_state(self, session_id: str) -> bool:
        """Delete an archived state from MinIO.

        Args:
            session_id: Session identifier

        Returns:
            True if deleted (or didn't exist)
        """
        try:
            await self._ensure_bucket_exists()

            object_key = self._get_state_object_key(session_id)
            loop = asyncio.get_event_loop()

            await loop.run_in_executor(
                None,
                lambda: self.minio_client.remove_object(self.bucket_name, object_key),
            )

            logger.debug("Deleted archived state", session_id=session_id[:12])
            return True

        except S3Error as e:
            if e.code == "NoSuchKey":
                return True  # Already doesn't exist
            logger.error(
                "Failed to delete archived state",
                session_id=session_id[:12],
                error=str(e),
            )
            return False
        except Exception as e:
            logger.error(
                "Failed to delete archived state",
                session_id=session_id[:12],
                error=str(e),
            )
            return False

    async def has_archived_state(self, session_id: str) -> bool:
        """Check if a session has archived state in MinIO.

        Args:
            session_id: Session identifier

        Returns:
            True if archived state exists
        """
        try:
            await self._ensure_bucket_exists()

            object_key = self._get_state_object_key(session_id)
            loop = asyncio.get_event_loop()

            try:
                await loop.run_in_executor(
                    None,
                    lambda: self.minio_client.stat_object(self.bucket_name, object_key),
                )
                return True
            except S3Error as e:
                if e.code == "NoSuchKey":
                    return False
                raise

        except Exception as e:
            logger.error(
                "Failed to check archived state",
                session_id=session_id[:12],
                error=str(e),
            )
            return False

    async def archive_inactive_states(self) -> Dict[str, Any]:
        """Archive inactive states from Redis to MinIO.

        This is the main archival task that runs periodically.
        It finds states with low TTL (indicating inactivity) and archives them.

        Returns:
            Summary of archival operation
        """
        if not settings.state_archive_enabled:
            return {"archived": 0, "skipped": "archival disabled"}

        summary = {
            "archived": 0,
            "failed": 0,
            "already_archived": 0,
        }

        try:
            # Find states ready for archival
            states_to_archive = await self.state_service.get_states_for_archival()

            for session_id, remaining_ttl, size in states_to_archive:
                try:
                    # Check if already archived
                    if await self.has_archived_state(session_id):
                        summary["already_archived"] += 1
                        continue

                    # Get the state data
                    state_data = await self.state_service.get_state(session_id)
                    if not state_data:
                        continue

                    # Archive to MinIO
                    if await self.archive_state(session_id, state_data):
                        summary["archived"] += 1
                    else:
                        summary["failed"] += 1

                except Exception as e:
                    logger.warning(
                        "Failed to archive individual state",
                        session_id=session_id[:12],
                        error=str(e),
                    )
                    summary["failed"] += 1

            if summary["archived"] > 0:
                logger.info(
                    "Completed state archival batch",
                    archived=summary["archived"],
                    failed=summary["failed"],
                    already_archived=summary["already_archived"],
                )

            return summary

        except Exception as e:
            logger.error("State archival batch failed", error=str(e))
            summary["error"] = str(e)
            return summary

    async def cleanup_expired_archives(self) -> Dict[str, Any]:
        """Clean up archived states that have exceeded their TTL.

        Returns:
            Summary of cleanup operation
        """
        if not settings.state_archive_enabled:
            return {"deleted": 0, "skipped": "archival disabled"}

        summary = {
            "deleted": 0,
            "failed": 0,
        }

        try:
            await self._ensure_bucket_exists()

            loop = asyncio.get_event_loop()
            prefix = f"{self.STATE_PREFIX}/"
            ttl_days = settings.state_archive_ttl_days
            cutoff = datetime.now(timezone.utc).timestamp() - (ttl_days * 24 * 3600)

            # List all archived states
            objects = await loop.run_in_executor(
                None,
                lambda: list(
                    self.minio_client.list_objects(
                        self.bucket_name, prefix=prefix, recursive=True
                    )
                ),
            )

            for obj in objects:
                try:
                    # Check object age
                    if obj.last_modified and obj.last_modified.timestamp() < cutoff:
                        # Extract session_id from path
                        parts = obj.object_name.split("/")
                        if len(parts) >= 2:
                            session_id = parts[1]
                            if await self.delete_archived_state(session_id):
                                summary["deleted"] += 1
                            else:
                                summary["failed"] += 1

                except Exception as e:
                    logger.warning(
                        "Failed to cleanup archived state",
                        object_name=obj.object_name,
                        error=str(e),
                    )
                    summary["failed"] += 1

            if summary["deleted"] > 0:
                logger.info(
                    "Cleaned up expired archived states",
                    deleted=summary["deleted"],
                    failed=summary["failed"],
                )

            return summary

        except Exception as e:
            logger.error("Archive cleanup failed", error=str(e))
            summary["error"] = str(e)
            return summary

    # ===== Hash-indexed state archival for state-file linking =====

    def _get_state_by_hash_object_key(self, state_hash: str) -> str:
        """Generate MinIO object key for a hash-indexed state."""
        return f"{self.STATE_BY_HASH_PREFIX}/{state_hash}/state.dat"

    async def archive_state_by_hash(self, state_hash: str, state_data: str) -> bool:
        """Archive a state indexed by its hash to MinIO.

        Args:
            state_hash: SHA256 hash of the state
            state_data: Base64-encoded state data

        Returns:
            True if archived successfully
        """
        try:
            await self._ensure_bucket_exists()

            object_key = self._get_state_by_hash_object_key(state_hash)
            state_bytes = state_data.encode("utf-8")

            # Create metadata
            metadata = {
                "archived_at": datetime.now(timezone.utc).isoformat(),
                "original_size": str(len(state_bytes)),
                "state_hash": state_hash,
            }

            # Upload to MinIO
            loop = asyncio.get_event_loop()
            data_stream = io.BytesIO(state_bytes)

            await loop.run_in_executor(
                None,
                lambda: self.minio_client.put_object(
                    self.bucket_name,
                    object_key,
                    data_stream,
                    len(state_bytes),
                    content_type="application/octet-stream",
                    metadata=metadata,
                ),
            )

            logger.debug(
                "Archived state by hash to MinIO",
                hash=state_hash[:12],
                size_bytes=len(state_bytes),
            )
            return True

        except Exception as e:
            logger.error(
                "Failed to archive state by hash",
                hash=state_hash[:12],
                error=str(e),
            )
            return False

    async def restore_state_by_hash(self, state_hash: str) -> Optional[str]:
        """Restore a state from MinIO by its hash.

        If found, the state is also saved back to Redis for fast access.

        Args:
            state_hash: SHA256 hash of the state

        Returns:
            Base64-encoded state data, or None if not found
        """
        try:
            await self._ensure_bucket_exists()

            object_key = self._get_state_by_hash_object_key(state_hash)
            loop = asyncio.get_event_loop()

            try:
                response = await loop.run_in_executor(
                    None,
                    lambda: self.minio_client.get_object(self.bucket_name, object_key),
                )
                state_bytes = response.read()
                response.close()
                response.release_conn()
            except S3Error as e:
                if e.code == "NoSuchKey":
                    logger.debug(
                        "No archived state found by hash", hash=state_hash[:12]
                    )
                    return None
                raise

            state_data = state_bytes.decode("utf-8")

            # Restore to Redis for fast access
            await self.state_service.save_state_by_hash(
                state_hash, state_data, ttl_seconds=settings.state_ttl_seconds
            )

            logger.debug(
                "Restored state by hash from MinIO",
                hash=state_hash[:12],
                size_bytes=len(state_bytes),
            )
            return state_data

        except Exception as e:
            logger.error(
                "Failed to restore state by hash",
                hash=state_hash[:12],
                error=str(e),
            )
            return None

    async def has_archived_state_by_hash(self, state_hash: str) -> bool:
        """Check if a state with this hash is archived in MinIO.

        Args:
            state_hash: SHA256 hash of the state

        Returns:
            True if archived state exists
        """
        try:
            await self._ensure_bucket_exists()

            object_key = self._get_state_by_hash_object_key(state_hash)
            loop = asyncio.get_event_loop()

            try:
                await loop.run_in_executor(
                    None,
                    lambda: self.minio_client.stat_object(self.bucket_name, object_key),
                )
                return True
            except S3Error as e:
                if e.code == "NoSuchKey":
                    return False
                raise

        except Exception as e:
            logger.error(
                "Failed to check archived state by hash",
                hash=state_hash[:12],
                error=str(e),
            )
            return False
