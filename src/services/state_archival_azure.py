"""State archival service for Azure Blob Storage cold storage.

This service handles archiving Python session states from Redis to Azure Blob Storage
for long-term storage, and restoring them on demand.

Hybrid storage architecture:
- Hot storage: Redis with 2-hour TTL (fast access)
- Cold storage: Azure Blob Storage with 7-day TTL (long-term archival)
"""

import asyncio
import io
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import structlog
from azure.storage.blob import BlobServiceClient, ContainerClient
from azure.core.exceptions import ResourceNotFoundError, ResourceExistsError

from ..config import settings
from .state import StateService


logger = structlog.get_logger(__name__)


class AzureStateArchivalService:
    """Manages archiving and restoring Python session states to/from Azure Blob Storage.

    States are stored under the path:
        states/{session_id}/state.dat
    """

    STATE_PREFIX = "states"

    def __init__(
        self,
        state_service: Optional[StateService] = None,
        connection_string: Optional[str] = None,
        container_name: Optional[str] = None,
    ):
        """Initialize the Azure archival service.

        Args:
            state_service: StateService instance for Redis operations
            connection_string: Azure Storage connection string
            container_name: Blob container name
        """
        self.state_service = state_service or StateService()

        # Initialize Azure Blob Storage client
        conn_str = connection_string or getattr(
            settings, 'azure_storage_connection_string', None
        )
        if conn_str:
            self.blob_service_client = BlobServiceClient.from_connection_string(conn_str)
        else:
            raise ValueError(
                "Azure Blob Storage connection string required. "
                "Set AZURE_STORAGE_CONNECTION_STRING environment variable."
            )

        self.container_name = container_name or getattr(
            settings, 'azure_storage_container', 'code-interpreter-files'
        )
        self.container_client = self.blob_service_client.get_container_client(
            self.container_name
        )
        self._container_checked = False

    def _get_state_blob_name(self, session_id: str) -> str:
        """Generate blob name for a session state."""
        return f"{self.STATE_PREFIX}/{session_id}/state.dat"

    async def _ensure_container_exists(self) -> None:
        """Ensure the blob container exists."""
        if self._container_checked:
            return

        try:
            loop = asyncio.get_event_loop()
            exists = await loop.run_in_executor(
                None, self.container_client.exists
            )

            if not exists:
                await loop.run_in_executor(
                    None, self.container_client.create_container
                )
                logger.info(
                    "Created Azure Blob container for state archival",
                    container=self.container_name,
                )

            self._container_checked = True

        except ResourceExistsError:
            self._container_checked = True
        except Exception as e:
            logger.error(
                "Failed to ensure container exists",
                error=str(e),
                container=self.container_name,
            )
            raise

    async def archive_state(self, session_id: str, state_data: str) -> bool:
        """Archive a session state to Azure Blob Storage.

        Args:
            session_id: Session identifier
            state_data: Base64-encoded state data (already lz4 compressed)

        Returns:
            True if archived successfully
        """
        try:
            await self._ensure_container_exists()

            blob_name = self._get_state_blob_name(session_id)
            state_bytes = state_data.encode("utf-8")

            # Create metadata
            metadata = {
                "archived_at": datetime.now(timezone.utc).isoformat(),
                "original_size": str(len(state_bytes)),
                "session_id": session_id,
            }

            loop = asyncio.get_event_loop()
            blob_client = self.container_client.get_blob_client(blob_name)

            await loop.run_in_executor(
                None,
                lambda: blob_client.upload_blob(
                    io.BytesIO(state_bytes),
                    overwrite=True,
                    metadata=metadata,
                ),
            )

            logger.info(
                "Archived state to Azure Blob Storage",
                session_id=session_id[:12],
                size_bytes=len(state_bytes),
                blob_name=blob_name,
            )
            return True

        except Exception as e:
            logger.error(
                "Failed to archive state",
                session_id=session_id[:12],
                error=str(e),
            )
            return False

    async def restore_state(self, session_id: str) -> Optional[str]:
        """Restore a session state from Azure Blob Storage.

        If found, the state is also saved back to Redis for fast access.

        Args:
            session_id: Session identifier

        Returns:
            Base64-encoded state data, or None if not found
        """
        try:
            await self._ensure_container_exists()

            blob_name = self._get_state_blob_name(session_id)
            loop = asyncio.get_event_loop()
            blob_client = self.container_client.get_blob_client(blob_name)

            try:
                download_stream = await loop.run_in_executor(
                    None, blob_client.download_blob
                )
                state_bytes = await loop.run_in_executor(
                    None, download_stream.readall
                )
            except ResourceNotFoundError:
                logger.debug("No archived state found", session_id=session_id[:12])
                return None

            state_data = state_bytes.decode("utf-8")

            # Restore to Redis for fast access
            await self.state_service.save_state(
                session_id, state_data, ttl_seconds=settings.state_ttl_seconds
            )

            logger.info(
                "Restored state from Azure Blob Storage",
                session_id=session_id[:12],
                size_bytes=len(state_bytes),
            )
            return state_data

        except Exception as e:
            logger.error(
                "Failed to restore state",
                session_id=session_id[:12],
                error=str(e),
            )
            return None

    async def delete_archived_state(self, session_id: str) -> bool:
        """Delete an archived state from Azure Blob Storage.

        Args:
            session_id: Session identifier

        Returns:
            True if deleted (or didn't exist)
        """
        try:
            await self._ensure_container_exists()

            blob_name = self._get_state_blob_name(session_id)
            loop = asyncio.get_event_loop()
            blob_client = self.container_client.get_blob_client(blob_name)

            try:
                await loop.run_in_executor(None, blob_client.delete_blob)
            except ResourceNotFoundError:
                pass  # Already doesn't exist

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
        """Check if a session has archived state in Azure Blob Storage.

        Args:
            session_id: Session identifier

        Returns:
            True if archived state exists
        """
        try:
            await self._ensure_container_exists()

            blob_name = self._get_state_blob_name(session_id)
            loop = asyncio.get_event_loop()
            blob_client = self.container_client.get_blob_client(blob_name)

            try:
                await loop.run_in_executor(
                    None, blob_client.get_blob_properties
                )
                return True
            except ResourceNotFoundError:
                return False

        except Exception as e:
            logger.error(
                "Failed to check archived state",
                session_id=session_id[:12],
                error=str(e),
            )
            return False

    async def archive_inactive_states(self) -> Dict[str, Any]:
        """Archive inactive states from Redis to Azure Blob Storage.

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
            await self._ensure_container_exists()

            loop = asyncio.get_event_loop()
            prefix = f"{self.STATE_PREFIX}/"
            ttl_days = settings.state_archive_ttl_days
            cutoff = datetime.now(timezone.utc).timestamp() - (ttl_days * 24 * 3600)

            # List all archived states
            blobs = await loop.run_in_executor(
                None,
                lambda: list(self.container_client.list_blobs(name_starts_with=prefix))
            )

            for blob in blobs:
                try:
                    # Check blob age
                    if blob.last_modified and blob.last_modified.timestamp() < cutoff:
                        # Extract session_id from path
                        parts = blob.name.split("/")
                        if len(parts) >= 2:
                            session_id = parts[1]
                            if await self.delete_archived_state(session_id):
                                summary["deleted"] += 1
                            else:
                                summary["failed"] += 1

                except Exception as e:
                    logger.warning(
                        "Failed to cleanup archived state",
                        blob_name=blob.name,
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
