"""File management service with S3-compatible storage integration."""

# Standard library imports
import asyncio
from datetime import datetime
from typing import List, Optional, Tuple, Dict, Any

# Third-party imports
import boto3
import redis.asyncio as redis
import structlog
from botocore.exceptions import ClientError

# Local application imports
from .interfaces import FileServiceInterface
from ..config import settings
from ..models import FileInfo, FileUploadRequest
from ..utils.id_generator import generate_file_id

logger = structlog.get_logger()


class FileService(FileServiceInterface):
    """File management service with S3 storage and Redis metadata."""

    def __init__(self):
        """Initialize the file service with S3 and Redis clients."""
        self.s3_client = boto3.client(
            "s3",
            endpoint_url=settings.s3.endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )

        # Initialize Redis client
        self.redis_client = redis.from_url(
            settings.get_redis_url(), decode_responses=True
        )

        self.bucket_name = settings.s3_bucket

    async def _ensure_bucket_exists(self) -> None:
        """Ensure the S3 bucket exists."""
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
                    logger.info("Created S3 bucket", bucket=self.bucket_name)
                else:
                    raise

        except ClientError as e:
            logger.error(
                "Failed to ensure bucket exists", error=str(e), bucket=self.bucket_name
            )
            raise

    def _get_file_key(
        self, session_id: str, file_id: str, file_type: str = "uploads"
    ) -> str:
        """Generate S3 object key for a file."""
        return f"sessions/{session_id}/{file_type}/{file_id}"

    def get_file_metadata_key(self, session_id: str, file_id: str) -> str:
        """Generate Redis key for file metadata."""
        return f"files:{session_id}:{file_id}"

    def _get_session_files_key(self, session_id: str) -> str:
        """Generate Redis key for session file list."""
        return f"session_files:{session_id}"

    def _get_file_links_key(self, session_id: str, file_id: str) -> str:
        """Generate Redis key for aliases that reference a source file."""
        return f"file_links:{session_id}:{file_id}"

    async def _register_link_reference(
        self,
        source_session_id: str,
        source_file_id: str,
        linked_session_id: str,
        linked_file_id: str,
    ) -> None:
        """Track a linked-input alias for cleanup safety."""
        links_key = self._get_file_links_key(source_session_id, source_file_id)
        ttl_seconds = settings.get_session_ttl_minutes() * 60
        await self.redis_client.sadd(links_key, f"{linked_session_id}:{linked_file_id}")
        await self.redis_client.expire(links_key, ttl_seconds)

    async def _remove_link_reference(
        self,
        source_session_id: str,
        source_file_id: str,
        linked_session_id: str,
        linked_file_id: str,
    ) -> None:
        """Remove a linked-input alias reference."""
        links_key = self._get_file_links_key(source_session_id, source_file_id)
        await self.redis_client.srem(links_key, f"{linked_session_id}:{linked_file_id}")

    async def _has_link_references(self, session_id: str, file_id: str) -> bool:
        """Return True when other session aliases still reference a file."""
        links_key = self._get_file_links_key(session_id, file_id)
        return bool(await self.redis_client.smembers(links_key))

    async def _delete_object(self, object_key: str) -> None:
        """Delete a backing object from S3."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self.s3_client.delete_object(
                Bucket=self.bucket_name, Key=object_key
            ),
        )

    async def _find_linked_file(
        self, target_session_id: str, source_session_id: str, source_file_id: str
    ) -> Optional[str]:
        """Return an existing linked-input alias for the given source file."""
        session_files_key = self._get_session_files_key(target_session_id)
        file_ids = await self.redis_client.smembers(session_files_key)

        for candidate_file_id in file_ids:
            metadata = await self.get_file_metadata(
                target_session_id, candidate_file_id
            )
            if not metadata:
                continue

            if (
                metadata.get("type") == "linked_input"
                and metadata.get("source_session_id") == source_session_id
                and metadata.get("source_file_id") == source_file_id
            ):
                return candidate_file_id

        return None

    async def _store_file_metadata(
        self, session_id: str, file_id: str, metadata: Dict[str, Any]
    ) -> None:
        """Store file metadata in Redis."""
        try:
            metadata_key = self.get_file_metadata_key(session_id, file_id)
            session_files_key = self._get_session_files_key(session_id)

            # Store file metadata
            await self.redis_client.hset(metadata_key, mapping=metadata)

            # Set TTL for metadata (same as session TTL)
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

    async def get_file_metadata(
        self, session_id: str, file_id: str
    ) -> Optional[Dict[str, Any]]:
        """Retrieve file metadata from Redis."""
        try:
            metadata_key = self.get_file_metadata_key(session_id, file_id)
            metadata = await self.redis_client.hgetall(metadata_key)

            if not metadata:
                return None

            # Convert string values back to appropriate types
            if "size" in metadata:
                metadata["size"] = int(metadata["size"])
            if "created_at" in metadata and isinstance(metadata["created_at"], str):
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
            metadata_key = self.get_file_metadata_key(session_id, file_id)
            session_files_key = self._get_session_files_key(session_id)

            # Delete metadata
            await self.redis_client.delete(metadata_key)

            # Remove from session file list
            await self.redis_client.srem(session_files_key, file_id)

        except Exception as e:
            logger.error(
                "Failed to delete file metadata",
                error=str(e),
                session_id=session_id,
                file_id=file_id,
            )
            raise

    def validate_uploads(
        self,
        filenames: List[str],
        file_sizes: List[Optional[int]],
    ) -> Optional[Tuple[int, str]]:
        """Validate upload files against size, count, and type restrictions.

        Args:
            filenames: List of filenames to validate
            file_sizes: List of file sizes (may contain None for unknown sizes)

        Returns:
            None if valid, or (http_status_code, error_message) tuple if invalid
        """
        for filename, size in zip(filenames, file_sizes):
            if size and size > settings.max_file_size_mb * 1024 * 1024:
                return (
                    413,
                    f"File {filename} exceeds maximum size of {settings.max_file_size_mb}MB",
                )

        if len(filenames) > settings.max_files_per_session:
            return (
                413,
                f"Too many files. Maximum {settings.max_files_per_session} files allowed",
            )

        for filename in filenames:
            if not settings.is_file_allowed(filename or ""):
                return (415, f"File type not allowed: {filename}")

        return None

    async def upload_file(
        self, session_id: str, request: FileUploadRequest
    ) -> Tuple[str, str]:
        """Generate upload URL for a file. Returns (file_id, upload_url)."""
        await self._ensure_bucket_exists()

        # Generate unique file ID
        file_id = generate_file_id()

        # Generate S3 object key
        object_key = self._get_file_key(session_id, file_id)

        try:
            # Generate presigned upload URL (expires in 1 hour)
            loop = asyncio.get_event_loop()
            upload_url = await loop.run_in_executor(
                None,
                lambda: self.s3_client.generate_presigned_url(
                    "put_object",
                    Params={"Bucket": self.bucket_name, "Key": object_key},
                    ExpiresIn=3600,
                ),
            )

            # Store initial metadata
            metadata = {
                "file_id": file_id,
                "filename": request.filename,
                "content_type": request.content_type or "application/octet-stream",
                "object_key": object_key,
                "session_id": session_id,
                "created_at": datetime.utcnow().isoformat(),
                "size": 0,  # Will be updated when upload is confirmed
                "path": f"/{request.filename}",
            }

            await self._store_file_metadata(session_id, file_id, metadata)

            logger.debug(
                "Generated file upload URL",
                session_id=session_id,
                file_id=file_id,
                filename=request.filename,
            )

            return file_id, upload_url

        except ClientError as e:
            logger.error(
                "Failed to generate upload URL", error=str(e), session_id=session_id
            )
            raise

    async def confirm_upload(self, session_id: str, file_id: str) -> FileInfo:
        """Confirm file upload completion and return file info."""
        metadata = await self.get_file_metadata(session_id, file_id)
        if not metadata:
            raise ValueError(f"File {file_id} not found in session {session_id}")

        object_key = metadata["object_key"]

        try:
            # Get object info to confirm upload and get size
            loop = asyncio.get_event_loop()
            head = await loop.run_in_executor(
                None,
                lambda: self.s3_client.head_object(
                    Bucket=self.bucket_name, Key=object_key
                ),
            )

            file_size = head["ContentLength"]

            # Update metadata with actual file size
            metadata["size"] = file_size
            await self._store_file_metadata(session_id, file_id, metadata)

            logger.debug(
                "Confirmed file upload",
                session_id=session_id,
                file_id=file_id,
                size=file_size,
            )

            return FileInfo(
                file_id=file_id,
                filename=metadata["filename"],
                size=file_size,
                content_type=metadata["content_type"],
                created_at=metadata["created_at"],
                path=metadata["path"],
            )

        except ClientError as e:
            logger.error(
                "Failed to confirm upload",
                error=str(e),
                session_id=session_id,
                file_id=file_id,
            )
            raise

    async def get_file_info(self, session_id: str, file_id: str) -> Optional[FileInfo]:
        """Get file information."""
        metadata = await self.get_file_metadata(session_id, file_id)
        if not metadata:
            return None

        return FileInfo(
            file_id=file_id,
            filename=metadata["filename"],
            size=metadata["size"],
            content_type=metadata["content_type"],
            created_at=metadata["created_at"],
            path=metadata["path"],
            original_filename=metadata.get("original_filename"),
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

            # Sort by creation time
            files.sort(key=lambda f: f.created_at)

            return files

        except Exception as e:
            logger.error("Failed to list files", error=str(e), session_id=session_id)
            return []

    async def link_file_into_session(
        self, target_session_id: str, source_session_id: str, source_file_id: str
    ) -> Optional[FileInfo]:
        """Create or reuse a read-only linked alias in the target session."""
        source_metadata = await self.get_file_metadata(
            source_session_id, source_file_id
        )
        if not source_metadata:
            logger.warning(
                "Cannot link missing source file",
                source_session_id=source_session_id,
                source_file_id=source_file_id,
                target_session_id=target_session_id,
            )
            return None

        existing_linked_file_id = await self._find_linked_file(
            target_session_id, source_session_id, source_file_id
        )
        if existing_linked_file_id:
            return await self.get_file_info(target_session_id, existing_linked_file_id)

        linked_file_id = generate_file_id()
        metadata = {
            "file_id": linked_file_id,
            "filename": source_metadata["filename"],
            "content_type": source_metadata["content_type"],
            "object_key": source_metadata["object_key"],
            "session_id": target_session_id,
            "created_at": datetime.utcnow().isoformat(),
            "size": source_metadata["size"],
            "path": source_metadata["path"],
            "type": "linked_input",
            "source_session_id": source_session_id,
            "source_file_id": source_file_id,
            "is_read_only": "1",
            "original_filename": source_metadata.get(
                "original_filename", source_metadata["filename"]
            ),
        }

        await self._store_file_metadata(target_session_id, linked_file_id, metadata)
        await self._register_link_reference(
            source_session_id,
            source_file_id,
            target_session_id,
            linked_file_id,
        )

        logger.debug(
            "Linked file into session",
            target_session_id=target_session_id,
            linked_file_id=linked_file_id,
            source_session_id=source_session_id,
            source_file_id=source_file_id,
        )

        return FileInfo(
            file_id=linked_file_id,
            filename=metadata["filename"],
            size=metadata["size"],
            content_type=metadata["content_type"],
            created_at=datetime.fromisoformat(metadata["created_at"]),
            path=metadata["path"],
            original_filename=metadata.get("original_filename"),
        )

    async def download_file(self, session_id: str, file_id: str) -> Optional[str]:
        """Generate download URL for a file."""
        metadata = await self.get_file_metadata(session_id, file_id)
        if not metadata:
            return None

        object_key = metadata["object_key"]

        try:
            # Generate presigned download URL (expires in 1 hour)
            loop = asyncio.get_event_loop()
            download_url = await loop.run_in_executor(
                None,
                lambda: self.s3_client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self.bucket_name, "Key": object_key},
                    ExpiresIn=3600,
                ),
            )

            return download_url

        except ClientError as e:
            logger.error(
                "Failed to generate download URL",
                error=str(e),
                session_id=session_id,
                file_id=file_id,
            )
            return None

    async def delete_file(self, session_id: str, file_id: str) -> bool:
        """Delete a file from the session."""
        metadata = await self.get_file_metadata(session_id, file_id)
        if not metadata:
            return False

        if metadata.get("type") == "linked_input":
            await self._delete_file_metadata(session_id, file_id)
            await self._remove_link_reference(
                metadata["source_session_id"],
                metadata["source_file_id"],
                session_id,
                file_id,
            )
            logger.debug(
                "Deleted linked file alias",
                session_id=session_id,
                file_id=file_id,
            )

            source_metadata = await self.get_file_metadata(
                metadata["source_session_id"],
                metadata["source_file_id"],
            )
            if source_metadata is None and not await self._has_link_references(
                metadata["source_session_id"],
                metadata["source_file_id"],
            ):
                try:
                    await self._delete_object(metadata["object_key"])
                    logger.debug(
                        "Deleted orphaned shared object after final alias cleanup",
                        source_session_id=metadata["source_session_id"],
                        source_file_id=metadata["source_file_id"],
                        object_key=metadata["object_key"],
                    )
                except ClientError as e:
                    logger.warning(
                        "Failed to delete orphaned shared object",
                        source_session_id=metadata["source_session_id"],
                        source_file_id=metadata["source_file_id"],
                        object_key=metadata["object_key"],
                        error=str(e),
                    )
            return True

        if await self._has_link_references(session_id, file_id):
            await self._delete_file_metadata(session_id, file_id)
            logger.debug(
                "Deleted file metadata but retained shared object",
                session_id=session_id,
                file_id=file_id,
            )
            return True

        object_key = metadata["object_key"]

        try:
            await self._delete_object(object_key)

            # Delete metadata from Redis
            await self._delete_file_metadata(session_id, file_id)

            logger.debug("Deleted file", session_id=session_id, file_id=file_id)
            return True

        except ClientError as e:
            logger.error(
                "Failed to delete file",
                error=str(e),
                session_id=session_id,
                file_id=file_id,
            )
            return False

    async def cleanup_session_files(self, session_id: str) -> int:
        """Clean up all files for a session. Returns count of deleted files."""
        try:
            session_files_key = self._get_session_files_key(session_id)
            file_ids = await self.redis_client.smembers(session_files_key)

            deleted_count = 0
            for file_id in file_ids:
                if await self.delete_file(session_id, file_id):
                    deleted_count += 1

            # Clean up session files set
            await self.redis_client.delete(session_files_key)

            # If no files were tracked in Redis, fall back to prefix-based deletion
            if deleted_count == 0:
                try:
                    loop = asyncio.get_event_loop()
                    prefixes = [
                        f"sessions/{session_id}/uploads/",
                        f"sessions/{session_id}/outputs/",
                    ]
                    for prefix in prefixes:

                        def _list_prefix(p: str = prefix) -> list:
                            return list(
                                self.s3_client.get_paginator("list_objects_v2")
                                .paginate(Bucket=self.bucket_name, Prefix=p)
                                .search("Contents[]")
                            )

                        objects = await loop.run_in_executor(None, _list_prefix)
                        for entry in objects:
                            if entry is None:
                                continue
                            key = entry["Key"]

                            def _delete(k: str = key) -> None:
                                self.s3_client.delete_object(
                                    Bucket=self.bucket_name, Key=k
                                )

                            await loop.run_in_executor(None, _delete)
                            deleted_count += 1
                except Exception as e:
                    logger.error(
                        "Prefix-based S3 cleanup failed",
                        session_id=session_id,
                        error=str(e),
                    )

            logger.debug(
                "Cleaned up session files",
                session_id=session_id,
                deleted_count=deleted_count,
            )
            return deleted_count

        except Exception as e:
            logger.error(
                "Failed to cleanup session files", error=str(e), session_id=session_id
            )
            return 0

    async def store_execution_output_file(
        self,
        session_id: str,
        filename: str,
        content: bytes,
    ) -> str:
        """Store a file generated during code execution.

        Args:
            session_id: Session identifier
            filename: Name of the file
            content: File content as bytes

        Returns:
            The generated file_id
        """
        await self._ensure_bucket_exists()

        # Generate unique file ID for output file
        file_id = generate_file_id()

        # Use outputs directory for execution-generated files
        object_key = self._get_file_key(session_id, file_id, "outputs")

        try:
            import io

            content_stream = io.BytesIO(content)

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.s3_client.put_object(
                    Bucket=self.bucket_name,
                    Key=object_key,
                    Body=content_stream,
                    ContentLength=len(content),
                ),
            )

            now = datetime.utcnow()

            metadata = {
                "file_id": file_id,
                "filename": filename,
                "content_type": "application/octet-stream",
                "object_key": object_key,
                "session_id": session_id,
                "created_at": now.isoformat(),
                "size": len(content),
                "path": f"/outputs/{filename}",
                "type": "output",
            }

            await self._store_file_metadata(session_id, file_id, metadata)

            logger.debug(
                "Stored execution output file",
                session_id=session_id,
                file_id=file_id,
                filename=filename,
                size=len(content),
            )

            return file_id

        except ClientError as e:
            logger.error(
                "Failed to store output file",
                error=str(e),
                session_id=session_id,
                filename=filename,
            )
            raise

    async def get_file_content(self, session_id: str, file_id: str) -> Optional[bytes]:
        """Get file content directly (for internal use)."""
        metadata = await self.get_file_metadata(session_id, file_id)
        if not metadata:
            return None

        object_key = metadata["object_key"]

        try:
            loop = asyncio.get_event_loop()

            def _download():
                response = self.s3_client.get_object(
                    Bucket=self.bucket_name, Key=object_key
                )
                return response["Body"].read()

            content = await loop.run_in_executor(None, _download)
            return content

        except ClientError as e:
            logger.error(
                "Failed to get file content",
                error=str(e),
                session_id=session_id,
                file_id=file_id,
            )
            return None

    async def stream_file_to_path(
        self, session_id: str, file_id: str, dest_path: str
    ) -> bool:
        """Stream file content from S3 directly to a local file path.

        Uses boto3's download_file for efficient disk-to-disk transfer
        without loading the entire file into memory. Runs in a thread
        pool executor to avoid blocking the async event loop.

        Args:
            session_id: Session identifier
            file_id: File identifier
            dest_path: Local filesystem path to write the file to

        Returns:
            True if successful, False otherwise
        """
        metadata = await self.get_file_metadata(session_id, file_id)
        if not metadata:
            return False

        object_key = metadata["object_key"]

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.s3_client.download_file(
                    self.bucket_name, object_key, dest_path
                ),
            )
            return True
        except ClientError as e:
            logger.error(
                "Failed to stream file to path",
                error=str(e),
                session_id=session_id,
                file_id=file_id,
                dest_path=dest_path,
            )
            return False

    async def store_uploaded_file(
        self,
        session_id: str,
        filename: str,
        content: bytes,
        content_type: Optional[str] = None,
        is_agent_file: bool = False,
        is_read_only: bool = False,
        original_filename: Optional[str] = None,
    ) -> str:
        """Store an uploaded file directly.

        Args:
            session_id: Session identifier
            filename: Sanitized filename used for storage and sandbox mounting
            content: File content as bytes
            content_type: MIME type of the file
            is_agent_file: If True, marks the file as read-only (agent-assigned)
            is_read_only: If True, mounted file should be chmod 444 in sandbox
            original_filename: Pre-sanitization filename for metadata recovery

        Returns:
            The generated file_id
        """
        await self._ensure_bucket_exists()

        # Generate unique file ID
        file_id = generate_file_id()

        # Generate S3 object key
        object_key = self._get_file_key(session_id, file_id, "uploads")

        try:
            from io import BytesIO

            content_stream = BytesIO(content)

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.s3_client.put_object(
                    Bucket=self.bucket_name,
                    Key=object_key,
                    Body=content_stream,
                    ContentLength=len(content),
                    ContentType=content_type or "application/octet-stream",
                ),
            )

            # Store metadata
            metadata = {
                "file_id": file_id,
                "filename": filename,
                "content_type": content_type or "application/octet-stream",
                "object_key": object_key,
                "session_id": session_id,
                "created_at": datetime.utcnow().isoformat(),
                "size": len(content),
                "path": f"/{filename}",
                "type": "upload",
                "is_agent_file": ("1" if is_agent_file else "0"),
                "is_read_only": "1" if (is_read_only or is_agent_file) else "0",
                "original_filename": original_filename or filename,
            }

            await self._store_file_metadata(session_id, file_id, metadata)

            logger.debug(
                "Stored uploaded file",
                session_id=session_id,
                file_id=file_id,
                filename=filename,
                size=len(content),
            )

            return file_id

        except ClientError as e:
            logger.error(
                "Failed to store uploaded file",
                error=str(e),
                session_id=session_id,
                filename=filename,
            )
            raise

    async def cleanup_orphan_objects(self, batch_limit: int = 1000) -> int:
        """Delete S3 objects under sessions/ whose sessions are not active in Redis.

        Safety guards:
        - Skip if the session index is empty (avoid mass-deletes on cold start).
        - Only delete objects older than the configured session TTL to prevent race conditions.

        Returns the count of deleted objects. The optional batch_limit bounds deletions per call.
        """
        try:
            # Fetch the current set of active session IDs from Redis
            active_session_ids = await self.redis_client.smembers("sessions:index")
            active_session_ids = active_session_ids or set()

            if not active_session_ids:
                logger.debug("Skipping orphan S3 cleanup: empty sessions index")
                return 0

            loop = asyncio.get_event_loop()

            # List all objects under the sessions/ prefix using paginator
            objects = await loop.run_in_executor(
                None,
                lambda: list(
                    self.s3_client.get_paginator("list_objects_v2")
                    .paginate(Bucket=self.bucket_name, Prefix="sessions/")
                    .search("Contents[]")
                ),
            )
            deleted_count = 0

            # Cache existence checks to minimize Redis round-trips
            checked_missing_sessions: Dict[str, bool] = {}

            # Determine age cutoff based on TTL
            ttl_minutes = settings.get_session_ttl_minutes()
            ttl_seconds = ttl_minutes * 60
            now_ts = datetime.utcnow().timestamp()

            for entry in objects:
                if entry is None:
                    continue
                if deleted_count >= batch_limit:
                    break

                object_key = entry.get("Key")
                if not object_key:
                    continue

                parts = object_key.split("/")
                if len(parts) < 3 or parts[0] != "sessions":
                    continue

                object_session_id = parts[1]

                try:
                    last_modified = entry.get("LastModified")
                    if last_modified is None:
                        continue
                    obj_ts = (
                        last_modified.timestamp()
                        if hasattr(last_modified, "timestamp")
                        else None
                    )
                    if obj_ts is None:
                        continue
                    if (now_ts - obj_ts) < ttl_seconds:
                        continue
                except Exception as e:
                    logger.debug(
                        "Could not evaluate object age for orphan cleanup",
                        object_key=object_key,
                        error=str(e),
                    )
                    continue

                if object_session_id in active_session_ids:
                    continue

                source_file_id = parts[3] if len(parts) >= 4 else None
                if source_file_id and await self._has_link_references(
                    object_session_id, source_file_id
                ):
                    continue

                if object_session_id not in checked_missing_sessions:
                    try:
                        exists = await self.redis_client.exists(
                            f"sessions:{object_session_id}"
                        )
                        checked_missing_sessions[object_session_id] = bool(exists)
                    except Exception as e:
                        logger.error(
                            "Redis check failed during orphan cleanup",
                            session_id=object_session_id,
                            error=str(e),
                        )
                        checked_missing_sessions[object_session_id] = False

                if checked_missing_sessions.get(object_session_id, False):
                    continue

                try:

                    def _delete_orphan(k: str = object_key) -> None:
                        self.s3_client.delete_object(Bucket=self.bucket_name, Key=k)

                    await loop.run_in_executor(None, _delete_orphan)
                    deleted_count += 1
                except Exception as e:
                    logger.error(
                        "Failed to delete orphan S3 object",
                        object_key=object_key,
                        error=str(e),
                    )

            if deleted_count > 0:
                logger.info("Deleted orphan S3 objects", deleted_count=deleted_count)
            else:
                logger.debug("No orphan S3 objects found")

            return deleted_count

        except Exception as e:
            logger.error("Orphan S3 objects cleanup failed", error=str(e))
            return 0

    async def update_file_content(
        self,
        session_id: str,
        file_id: str,
        content: bytes,
    ) -> bool:
        """Update the content of an existing file.

        Overwrites the S3 object and updates metadata. Used to persist
        in-place edits to mounted files after execution.

        Args:
            session_id: Session identifier
            file_id: File identifier
            content: New file content as bytes

        Returns:
            True if update was successful
        """
        try:
            # Get existing metadata to find object_key
            metadata = await self.get_file_metadata(session_id, file_id)
            if not metadata:
                logger.warning(
                    "File not found for content update",
                    session_id=session_id[:12],
                    file_id=file_id,
                )
                return False

            if metadata.get("is_read_only") == "1":
                logger.debug(
                    "Skipping update for read-only file",
                    session_id=session_id[:12],
                    file_id=file_id,
                )
                return False

            object_key = metadata.get("object_key")
            if not object_key:
                logger.warning(
                    "No object_key in file metadata",
                    session_id=session_id[:12],
                    file_id=file_id,
                )
                return False

            import io

            loop = asyncio.get_event_loop()
            content_stream = io.BytesIO(content)
            content_type = metadata.get("content_type", "application/octet-stream")

            await loop.run_in_executor(
                None,
                lambda: self.s3_client.put_object(
                    Bucket=self.bucket_name,
                    Key=object_key,
                    Body=content_stream,
                    ContentLength=len(content),
                    ContentType=content_type,
                ),
            )

            # Update metadata
            updates = {
                "size": len(content),
            }

            metadata_key = self.get_file_metadata_key(session_id, file_id)
            await self.redis_client.hset(metadata_key, mapping=updates)

            logger.debug(
                "Updated file content",
                session_id=session_id[:12],
                file_id=file_id,
                size=len(content),
            )
            return True

        except Exception as e:
            logger.error(
                "Failed to update file content",
                error=str(e),
                session_id=session_id,
                file_id=file_id,
            )
            return False

    async def close(self) -> None:
        """Close service connections."""
        try:
            await self.redis_client.close()
            logger.info("Closed file service connections")
        except Exception as e:
            logger.error("Error closing file service connections", error=str(e))
