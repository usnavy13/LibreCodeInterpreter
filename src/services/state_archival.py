"""State archival service for S3 cold storage.

This service handles archiving Python session states from Redis to S3
for long-term storage, and restoring them on demand.

Hybrid storage architecture:
- Hot storage: Redis with 2-hour TTL (fast access)
- Cold storage: S3 with configurable TTL (long-term archival)

When a state is accessed:
1. Check Redis first (hot storage)
2. If not found, check S3 (cold storage)
3. If found in S3, restore to Redis

States are archived to S3 when:
- TTL in Redis drops below archive_after_seconds threshold
- This indicates the session has been inactive for a while
"""

import asyncio
import io
from datetime import datetime, timezone
from typing import Optional, Dict, Any

import boto3
import structlog
from botocore.exceptions import ClientError

from ..config import settings
from .state import StateService

logger = structlog.get_logger(__name__)


class StateArchivalService:
    """Manages archiving and restoring Python session states to/from S3.

    States are stored in S3 under the path:
        states/{session_id}/state.dat

    Metadata is stored as S3 object metadata:
        - archived_at: ISO timestamp
        - original_size: Size before any host-side compression
        - session_id: The session identifier
    """

    STATE_PREFIX = "states"

    def __init__(
        self,
        state_service: Optional[StateService] = None,
        s3_client: Optional[Any] = None,
    ):
        """Initialize the archival service.

        Args:
            state_service: StateService instance for Redis operations
            s3_client: Optional boto3 S3 client (creates new one if not provided)
        """
        self.state_service = state_service or StateService()
        self.s3_client = s3_client or boto3.client(
            "s3",
            endpoint_url=settings.s3.endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )
        self.bucket_name = settings.s3_bucket
        self._bucket_checked = False

    def _get_state_object_key(self, session_id: str) -> str:
        """Generate S3 object key for a session state."""
        return f"{self.STATE_PREFIX}/{session_id}/state.dat"

    async def _ensure_bucket_exists(self) -> None:
        """Ensure the S3 bucket exists."""
        if self._bucket_checked:
            return

        try:
            loop = asyncio.get_event_loop()
            try:
                await loop.run_in_executor(
                    None,
                    lambda: self.s3_client.head_bucket(Bucket=self.bucket_name),
                )
            except ClientError as e:
                if e.response["Error"]["Code"] in ("404", "NoSuchBucket"):
                    await loop.run_in_executor(
                        None,
                        lambda: self.s3_client.create_bucket(Bucket=self.bucket_name),
                    )
                    logger.info(
                        "Created S3 bucket for state archival", bucket=self.bucket_name
                    )
                else:
                    raise

            self._bucket_checked = True

        except ClientError as e:
            logger.error(
                "Failed to ensure bucket exists", error=str(e), bucket=self.bucket_name
            )
            raise

    async def archive_state(self, session_id: str, state_data: str) -> bool:
        """Archive a session state to S3.

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

            metadata = {
                "archived_at": datetime.now(timezone.utc).isoformat(),
                "original_size": str(len(state_bytes)),
                "session_id": session_id,
            }

            loop = asyncio.get_event_loop()
            data_stream = io.BytesIO(state_bytes)

            await loop.run_in_executor(
                None,
                lambda: self.s3_client.put_object(
                    Bucket=self.bucket_name,
                    Key=object_key,
                    Body=data_stream,
                    ContentLength=len(state_bytes),
                    ContentType="application/octet-stream",
                    Metadata=metadata,
                ),
            )

            logger.info(
                "Archived state to S3",
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
        """Restore a session state from S3.

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

            try:
                response = await loop.run_in_executor(
                    None,
                    lambda: self.s3_client.get_object(
                        Bucket=self.bucket_name, Key=object_key
                    ),
                )
                state_bytes = response["Body"].read()
            except ClientError as e:
                if e.response["Error"]["Code"] == "NoSuchKey":
                    logger.debug("No archived state found", session_id=session_id[:12])
                    return None
                raise

            state_data = state_bytes.decode("utf-8")

            # Only restore to Redis if under the size threshold
            import base64 as _b64

            raw_size = len(_b64.b64decode(state_data))
            max_redis_bytes = settings.state_max_redis_size_mb * 1024 * 1024

            if raw_size <= max_redis_bytes:
                await self.state_service.save_state(
                    session_id, state_data, ttl_seconds=settings.state_ttl_seconds
                )
            else:
                await self.state_service.save_state_pointer(
                    session_id, state_data, ttl_seconds=settings.state_ttl_seconds
                )
                logger.info(
                    "State too large for Redis, kept in S3 only",
                    session_id=session_id[:12],
                    state_size_mb=round(raw_size / 1024 / 1024, 1),
                )

            logger.info(
                "Restored state from S3",
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
        """Delete an archived state from S3.

        Args:
            session_id: Session identifier

        Returns:
            True if deleted (or didn't exist)
        """
        try:
            await self._ensure_bucket_exists()

            object_key = self._get_state_object_key(session_id)
            loop = asyncio.get_event_loop()

            # boto3 delete_object is idempotent — no error on missing key
            await loop.run_in_executor(
                None,
                lambda: self.s3_client.delete_object(
                    Bucket=self.bucket_name, Key=object_key
                ),
            )

            logger.debug("Deleted archived state", session_id=session_id[:12])
            return True

        except Exception as e:
            logger.error(
                "Failed to delete archived state",
                session_id=session_id[:12],
                error=str(e),
            )
            return False

    async def has_archived_state(self, session_id: str) -> bool:
        """Check if a session has archived state in S3.

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
                    lambda: self.s3_client.head_object(
                        Bucket=self.bucket_name, Key=object_key
                    ),
                )
                return True
            except ClientError as e:
                if e.response["Error"]["Code"] == "404":
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
        """Archive inactive states from Redis to S3.

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
            states_to_archive = await self.state_service.get_states_for_archival()

            for session_id, remaining_ttl, size in states_to_archive:
                try:
                    if await self.has_archived_state(session_id):
                        summary["already_archived"] += 1
                        continue

                    state_data = await self.state_service.get_state(session_id)
                    if not state_data:
                        continue

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

            objects = await loop.run_in_executor(
                None,
                lambda: list(
                    self.s3_client.get_paginator("list_objects_v2")
                    .paginate(Bucket=self.bucket_name, Prefix=prefix)
                    .search("Contents[]")
                ),
            )

            for entry in objects:
                if entry is None:
                    continue
                try:
                    last_modified = entry.get("LastModified")
                    if last_modified and last_modified.timestamp() < cutoff:
                        parts = entry["Key"].split("/")
                        if len(parts) >= 2:
                            session_id = parts[1]
                            if await self.delete_archived_state(session_id):
                                summary["deleted"] += 1
                            else:
                                summary["failed"] += 1

                except Exception as e:
                    logger.warning(
                        "Failed to cleanup archived state",
                        object_name=entry.get("Key"),
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
