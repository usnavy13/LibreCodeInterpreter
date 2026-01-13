"""File management service with Azure Blob Storage integration.

This is the Azure Container Apps version that replaces MinIO with Azure Blob Storage.
"""

import asyncio
import io
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import redis.asyncio as redis
import structlog
from azure.storage.blob import (
    BlobServiceClient,
    ContainerClient,
    generate_blob_sas,
    BlobSasPermissions,
)
from azure.core.exceptions import ResourceNotFoundError, ResourceExistsError

from .interfaces import FileServiceInterface
from ..config import settings
from ..models import FileInfo, FileUploadRequest
from ..utils.id_generator import generate_file_id


logger = structlog.get_logger()


class AzureFileService(FileServiceInterface):
    """File management service with Azure Blob Storage and Redis metadata."""

    def __init__(
        self,
        connection_string: Optional[str] = None,
        account_url: Optional[str] = None,
        container_name: Optional[str] = None,
        redis_url: Optional[str] = None,
    ):
        """
        Initialize the Azure file service.

        Args:
            connection_string: Azure Storage connection string (preferred)
            account_url: Azure Storage account URL (alternative with DefaultAzureCredential)
            container_name: Blob container name
            redis_url: Redis connection URL
        """
        # Initialize Azure Blob Storage client
        if connection_string:
            self.blob_service_client = BlobServiceClient.from_connection_string(
                connection_string
            )
        elif account_url:
            from azure.identity import DefaultAzureCredential
            self.blob_service_client = BlobServiceClient(
                account_url=account_url,
                credential=DefaultAzureCredential(),
            )
        else:
            # Fall back to settings
            conn_str = getattr(settings, 'azure_storage_connection_string', None)
            if conn_str:
                self.blob_service_client = BlobServiceClient.from_connection_string(conn_str)
            else:
                raise ValueError(
                    "Azure Blob Storage connection string or account URL required. "
                    "Set AZURE_STORAGE_CONNECTION_STRING environment variable."
                )

        self.container_name = container_name or getattr(
            settings, 'azure_storage_container', 'code-interpreter-files'
        )

        # Get container client
        self.container_client = self.blob_service_client.get_container_client(
            self.container_name
        )

        # Initialize Redis client
        redis_url = redis_url or settings.get_redis_url()
        self.redis_client = redis.from_url(redis_url, decode_responses=True)

        # Cache for SAS token generation
        self._account_name = self.blob_service_client.account_name
        self._account_key = None  # Will be extracted from connection string if available

    async def _ensure_container_exists(self) -> None:
        """Ensure the blob container exists."""
        try:
            loop = asyncio.get_event_loop()
            exists = await loop.run_in_executor(
                None, self.container_client.exists
            )

            if not exists:
                await loop.run_in_executor(
                    None, self.container_client.create_container
                )
                logger.info("Created Azure Blob container", container=self.container_name)

        except ResourceExistsError:
            pass  # Container already exists
        except Exception as e:
            logger.error(
                "Failed to ensure container exists",
                error=str(e),
                container=self.container_name,
            )
            raise

    def _get_blob_name(
        self, session_id: str, file_id: str, file_type: str = "uploads"
    ) -> str:
        """Generate blob name for a file."""
        return f"sessions/{session_id}/{file_type}/{file_id}"

    def _get_file_metadata_key(self, session_id: str, file_id: str) -> str:
        """Generate Redis key for file metadata."""
        return f"files:{session_id}:{file_id}"

    def _get_session_files_key(self, session_id: str) -> str:
        """Generate Redis key for session file list."""
        return f"session_files:{session_id}"

    async def _store_file_metadata(
        self, session_id: str, file_id: str, metadata: Dict[str, Any]
    ) -> None:
        """Store file metadata in Redis."""
        try:
            metadata_key = self._get_file_metadata_key(session_id, file_id)
            session_files_key = self._get_session_files_key(session_id)

            # Store file metadata
            await self.redis_client.hset(metadata_key, mapping=metadata)

            # Set TTL for metadata
            ttl_seconds = settings.get_session_ttl_minutes() * 60
            await self.redis_client.expire(metadata_key, ttl_seconds)

            # Add file to session file list
            await self.redis_client.sadd(session_files_key, file_id)
            await self.redis_client.expire(session_files_key, ttl_seconds)

        except Exception as e:
            logger.error(
                "Failed to store file metadata",
                error=str(e),
                session_id=session_id,
                file_id=file_id,
            )
            raise

    async def _get_file_metadata(
        self, session_id: str, file_id: str
    ) -> Optional[Dict[str, Any]]:
        """Retrieve file metadata from Redis."""
        try:
            metadata_key = self._get_file_metadata_key(session_id, file_id)
            metadata = await self.redis_client.hgetall(metadata_key)

            if not metadata:
                return None

            # Convert string values back to appropriate types
            if "size" in metadata:
                metadata["size"] = int(metadata["size"])
            if "created_at" in metadata:
                metadata["created_at"] = datetime.fromisoformat(metadata["created_at"])

            return metadata

        except Exception as e:
            logger.error(
                "Failed to get file metadata",
                error=str(e),
                session_id=session_id,
                file_id=file_id,
            )
            return None

    async def _delete_file_metadata(self, session_id: str, file_id: str) -> None:
        """Delete file metadata from Redis."""
        try:
            metadata_key = self._get_file_metadata_key(session_id, file_id)
            session_files_key = self._get_session_files_key(session_id)

            await self.redis_client.delete(metadata_key)
            await self.redis_client.srem(session_files_key, file_id)

        except Exception as e:
            logger.error(
                "Failed to delete file metadata",
                error=str(e),
                session_id=session_id,
                file_id=file_id,
            )
            raise

    def _generate_sas_url(
        self,
        blob_name: str,
        permission: str = "r",
        expiry_hours: int = 1,
    ) -> str:
        """
        Generate a SAS URL for blob access.

        Args:
            blob_name: Name of the blob
            permission: "r" for read, "w" for write, "rw" for read/write
            expiry_hours: Hours until SAS expires

        Returns:
            Full SAS URL for the blob
        """
        # Get blob client
        blob_client = self.container_client.get_blob_client(blob_name)

        # Create SAS token
        # Note: For production, use user delegation SAS with DefaultAzureCredential
        # This example uses account key SAS for simplicity
        sas_permissions = BlobSasPermissions(
            read="r" in permission,
            write="w" in permission,
            create="w" in permission,
        )

        # Generate SAS token using the blob client's URL
        # In production, you'd use generate_blob_sas with account key or user delegation key
        expiry_time = datetime.now(timezone.utc) + timedelta(hours=expiry_hours)

        try:
            # Try to generate SAS with account key (requires connection string auth)
            sas_token = generate_blob_sas(
                account_name=self._account_name,
                container_name=self.container_name,
                blob_name=blob_name,
                account_key=self._get_account_key(),
                permission=sas_permissions,
                expiry=expiry_time,
            )
            return f"{blob_client.url}?{sas_token}"
        except Exception:
            # Fall back to returning blob URL (requires public access or managed identity)
            return blob_client.url

    def _get_account_key(self) -> Optional[str]:
        """Extract account key from connection string."""
        if self._account_key is None:
            try:
                # Parse connection string for account key
                conn_str = getattr(settings, 'azure_storage_connection_string', '')
                if 'AccountKey=' in conn_str:
                    parts = conn_str.split(';')
                    for part in parts:
                        if part.startswith('AccountKey='):
                            self._account_key = part.split('=', 1)[1]
                            break
            except Exception:
                pass
        return self._account_key

    async def upload_file(
        self, session_id: str, request: FileUploadRequest
    ) -> Tuple[str, str]:
        """Generate upload URL for a file. Returns (file_id, upload_url)."""
        await self._ensure_container_exists()

        file_id = generate_file_id()
        blob_name = self._get_blob_name(session_id, file_id)

        try:
            # Generate SAS URL for upload
            upload_url = self._generate_sas_url(blob_name, permission="w", expiry_hours=1)

            # Store initial metadata
            metadata = {
                "file_id": file_id,
                "filename": request.filename,
                "content_type": request.content_type or "application/octet-stream",
                "blob_name": blob_name,
                "session_id": session_id,
                "created_at": datetime.utcnow().isoformat(),
                "size": 0,
                "path": f"/{request.filename}",
            }

            await self._store_file_metadata(session_id, file_id, metadata)

            logger.info(
                "Generated file upload URL",
                session_id=session_id,
                file_id=file_id,
                filename=request.filename,
            )

            return file_id, upload_url

        except Exception as e:
            logger.error(
                "Failed to generate upload URL",
                error=str(e),
                session_id=session_id,
            )
            raise

    async def confirm_upload(self, session_id: str, file_id: str) -> FileInfo:
        """Confirm file upload completion and return file info."""
        metadata = await self._get_file_metadata(session_id, file_id)
        if not metadata:
            raise ValueError(f"File {file_id} not found in session {session_id}")

        blob_name = metadata["blob_name"]

        try:
            loop = asyncio.get_event_loop()
            blob_client = self.container_client.get_blob_client(blob_name)

            # Get blob properties to confirm upload and get size
            properties = await loop.run_in_executor(
                None, blob_client.get_blob_properties
            )

            # Update metadata with actual file size
            metadata["size"] = properties.size
            await self._store_file_metadata(session_id, file_id, metadata)

            logger.info(
                "Confirmed file upload",
                session_id=session_id,
                file_id=file_id,
                size=properties.size,
            )

            return FileInfo(
                file_id=file_id,
                filename=metadata["filename"],
                size=properties.size,
                content_type=metadata["content_type"],
                created_at=metadata["created_at"],
                path=metadata["path"],
            )

        except ResourceNotFoundError:
            raise ValueError(f"File {file_id} not found in blob storage")
        except Exception as e:
            logger.error(
                "Failed to confirm upload",
                error=str(e),
                session_id=session_id,
                file_id=file_id,
            )
            raise

    async def get_file_info(self, session_id: str, file_id: str) -> Optional[FileInfo]:
        """Get file information."""
        metadata = await self._get_file_metadata(session_id, file_id)
        if not metadata:
            return None

        return FileInfo(
            file_id=file_id,
            filename=metadata["filename"],
            size=metadata["size"],
            content_type=metadata["content_type"],
            created_at=metadata["created_at"],
            path=metadata["path"],
        )

    async def list_files(self, session_id: str) -> List[FileInfo]:
        """List all files in a session."""
        try:
            session_files_key = self._get_session_files_key(session_id)
            file_ids = await self.redis_client.smembers(session_files_key)

            files = []
            for file_id in file_ids:
                file_info = await self.get_file_info(session_id, file_id)
                if file_info:
                    files.append(file_info)

            files.sort(key=lambda f: f.created_at)
            return files

        except Exception as e:
            logger.error("Failed to list files", error=str(e), session_id=session_id)
            return []

    async def download_file(self, session_id: str, file_id: str) -> Optional[str]:
        """Generate download URL for a file."""
        metadata = await self._get_file_metadata(session_id, file_id)
        if not metadata:
            return None

        blob_name = metadata["blob_name"]

        try:
            download_url = self._generate_sas_url(blob_name, permission="r", expiry_hours=1)
            return download_url

        except Exception as e:
            logger.error(
                "Failed to generate download URL",
                error=str(e),
                session_id=session_id,
                file_id=file_id,
            )
            return None

    async def get_file_content(self, session_id: str, file_id: str) -> Optional[bytes]:
        """Get file content directly."""
        metadata = await self._get_file_metadata(session_id, file_id)
        if not metadata:
            return None

        blob_name = metadata["blob_name"]

        try:
            loop = asyncio.get_event_loop()
            blob_client = self.container_client.get_blob_client(blob_name)

            download_stream = await loop.run_in_executor(
                None, blob_client.download_blob
            )
            content = await loop.run_in_executor(
                None, download_stream.readall
            )

            return content

        except ResourceNotFoundError:
            return None
        except Exception as e:
            logger.error(
                "Failed to get file content",
                error=str(e),
                session_id=session_id,
                file_id=file_id,
            )
            return None

    async def delete_file(self, session_id: str, file_id: str) -> bool:
        """Delete a file from storage."""
        metadata = await self._get_file_metadata(session_id, file_id)
        if not metadata:
            return False

        blob_name = metadata["blob_name"]

        try:
            loop = asyncio.get_event_loop()
            blob_client = self.container_client.get_blob_client(blob_name)

            await loop.run_in_executor(
                None, blob_client.delete_blob
            )

            await self._delete_file_metadata(session_id, file_id)

            logger.info("Deleted file", session_id=session_id, file_id=file_id)
            return True

        except ResourceNotFoundError:
            # File doesn't exist in blob storage, clean up metadata anyway
            await self._delete_file_metadata(session_id, file_id)
            return True
        except Exception as e:
            logger.error(
                "Failed to delete file",
                error=str(e),
                session_id=session_id,
                file_id=file_id,
            )
            return False

    async def cleanup_session_files(self, session_id: str) -> int:
        """Clean up all files for a session."""
        try:
            session_files_key = self._get_session_files_key(session_id)
            file_ids = await self.redis_client.smembers(session_files_key)

            deleted_count = 0
            for file_id in file_ids:
                if await self.delete_file(session_id, file_id):
                    deleted_count += 1

            await self.redis_client.delete(session_files_key)

            # Also clean up by prefix in blob storage
            if deleted_count == 0:
                loop = asyncio.get_event_loop()
                prefixes = [
                    f"sessions/{session_id}/uploads/",
                    f"sessions/{session_id}/outputs/",
                ]
                for prefix in prefixes:
                    blobs = await loop.run_in_executor(
                        None,
                        lambda p=prefix: list(self.container_client.list_blobs(name_starts_with=p))
                    )
                    for blob in blobs:
                        blob_client = self.container_client.get_blob_client(blob.name)
                        try:
                            await loop.run_in_executor(None, blob_client.delete_blob)
                            deleted_count += 1
                        except Exception:
                            pass

            logger.info(
                "Cleaned up session files",
                session_id=session_id,
                deleted_count=deleted_count,
            )
            return deleted_count

        except Exception as e:
            logger.error(
                "Failed to cleanup session files",
                error=str(e),
                session_id=session_id,
            )
            return 0

    async def store_execution_output_file(
        self, session_id: str, filename: str, content: bytes
    ) -> str:
        """Store a file generated during code execution."""
        await self._ensure_container_exists()

        file_id = generate_file_id()
        blob_name = self._get_blob_name(session_id, file_id, "outputs")

        try:
            loop = asyncio.get_event_loop()
            blob_client = self.container_client.get_blob_client(blob_name)

            await loop.run_in_executor(
                None,
                lambda: blob_client.upload_blob(io.BytesIO(content), overwrite=True)
            )

            metadata = {
                "file_id": file_id,
                "filename": filename,
                "content_type": "application/octet-stream",
                "blob_name": blob_name,
                "session_id": session_id,
                "created_at": datetime.utcnow().isoformat(),
                "size": len(content),
                "path": f"/outputs/{filename}",
                "type": "output",
            }

            await self._store_file_metadata(session_id, file_id, metadata)

            logger.info(
                "Stored execution output file",
                session_id=session_id,
                file_id=file_id,
                filename=filename,
                size=len(content),
            )

            return file_id

        except Exception as e:
            logger.error(
                "Failed to store output file",
                error=str(e),
                session_id=session_id,
                filename=filename,
            )
            raise

    async def store_uploaded_file(
        self,
        session_id: str,
        filename: str,
        content: bytes,
        content_type: Optional[str] = None,
    ) -> str:
        """Store an uploaded file directly."""
        await self._ensure_container_exists()

        file_id = generate_file_id()
        blob_name = self._get_blob_name(session_id, file_id, "uploads")

        try:
            loop = asyncio.get_event_loop()
            blob_client = self.container_client.get_blob_client(blob_name)

            await loop.run_in_executor(
                None,
                lambda: blob_client.upload_blob(
                    io.BytesIO(content),
                    overwrite=True,
                    content_settings={
                        'content_type': content_type or 'application/octet-stream'
                    } if content_type else None
                )
            )

            metadata = {
                "file_id": file_id,
                "filename": filename,
                "content_type": content_type or "application/octet-stream",
                "blob_name": blob_name,
                "session_id": session_id,
                "created_at": datetime.utcnow().isoformat(),
                "size": len(content),
                "path": f"/{filename}",
                "type": "upload",
            }

            await self._store_file_metadata(session_id, file_id, metadata)

            logger.info(
                "Stored uploaded file",
                session_id=session_id,
                file_id=file_id,
                filename=filename,
                size=len(content),
            )

            return file_id

        except Exception as e:
            logger.error(
                "Failed to store uploaded file",
                error=str(e),
                session_id=session_id,
                filename=filename,
            )
            raise

    async def cleanup_orphan_objects(self, batch_limit: int = 1000) -> int:
        """Delete blobs whose sessions are not active in Redis."""
        try:
            active_session_ids = await self.redis_client.smembers("sessions:index")
            active_session_ids = active_session_ids or set()

            if not active_session_ids:
                logger.debug("Skipping orphan cleanup: empty sessions index")
                return 0

            loop = asyncio.get_event_loop()
            blobs = await loop.run_in_executor(
                None,
                lambda: list(self.container_client.list_blobs(name_starts_with="sessions/"))
            )

            deleted_count = 0
            checked_sessions: Dict[str, bool] = {}

            ttl_minutes = settings.get_session_ttl_minutes()
            ttl_seconds = ttl_minutes * 60
            now_ts = datetime.now(timezone.utc).timestamp()

            for blob in blobs:
                if deleted_count >= batch_limit:
                    break

                blob_name = blob.name
                parts = blob_name.split("/")
                if len(parts) < 3 or parts[0] != "sessions":
                    continue

                blob_session_id = parts[1]

                # Check age
                last_modified = blob.last_modified
                if last_modified:
                    obj_ts = last_modified.timestamp()
                    if (now_ts - obj_ts) < ttl_seconds:
                        continue

                if blob_session_id in active_session_ids:
                    continue

                if blob_session_id not in checked_sessions:
                    exists = await self.redis_client.exists(f"sessions:{blob_session_id}")
                    checked_sessions[blob_session_id] = bool(exists)

                if checked_sessions.get(blob_session_id, False):
                    continue

                try:
                    blob_client = self.container_client.get_blob_client(blob_name)
                    await loop.run_in_executor(None, blob_client.delete_blob)
                    deleted_count += 1
                except Exception as e:
                    logger.error(
                        "Failed to delete orphan blob",
                        blob_name=blob_name,
                        error=str(e),
                    )

            if deleted_count > 0:
                logger.info("Deleted orphan blobs", deleted_count=deleted_count)

            return deleted_count

        except Exception as e:
            logger.error("Orphan cleanup failed", error=str(e))
            return 0

    async def close(self) -> None:
        """Close service connections."""
        try:
            await self.redis_client.close()
            logger.info("Closed Azure file service connections")
        except Exception as e:
            logger.error("Error closing file service connections", error=str(e))
