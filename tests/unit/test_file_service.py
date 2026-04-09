"""Unit tests for the FileService."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.file import FileService


@pytest.fixture
def mock_minio_client():
    """Mock MinIO client."""
    client = MagicMock()
    client.bucket_exists = MagicMock(return_value=True)
    client.put_object = MagicMock()
    client.get_object = MagicMock()
    return client


@pytest.fixture
def mock_redis_client():
    """Mock Redis client."""
    client = AsyncMock()
    client.hgetall = AsyncMock(return_value={})
    client.hset = AsyncMock()
    client.hget = AsyncMock(return_value=None)
    client.sadd = AsyncMock()
    client.srem = AsyncMock()
    client.smembers = AsyncMock(return_value=set())
    client.expire = AsyncMock()
    client.delete = AsyncMock()
    client.close = AsyncMock()
    return client


@pytest.fixture
def file_service(mock_minio_client, mock_redis_client):
    """Create FileService with mocked clients."""
    with patch("src.services.file.Minio") as mock_minio_class:
        mock_minio_class.return_value = mock_minio_client
        with patch("src.services.file.redis.from_url") as mock_redis_from_url:
            mock_redis_from_url.return_value = mock_redis_client
            service = FileService()
            service.minio_client = mock_minio_client
            service.redis_client = mock_redis_client
            return service


class TestUpdateFileContent:
    """Tests for update_file_content method."""

    @pytest.mark.asyncio
    async def test_update_file_content_rejects_read_only_file(
        self, file_service, mock_minio_client, mock_redis_client
    ):
        """Read-only linked aliases must not overwrite the source object."""
        session_id = "test-session"
        file_id = "linked-file"

        mock_redis_client.hgetall.return_value = {
            "file_id": file_id,
            "filename": "report.csv",
            "object_key": "sessions/source/uploads/source-file",
            "content_type": "text/csv",
            "is_read_only": "1",
        }

        result = await file_service.update_file_content(
            session_id=session_id,
            file_id=file_id,
            content=b"modified",
        )

        assert result is False
        mock_minio_client.put_object.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_file_content_success(
        self, file_service, mock_minio_client, mock_redis_client
    ):
        """Test that update_file_content overwrites file in MinIO."""
        session_id = "test-session-123"
        file_id = "test-file-456"
        new_content = b"modified file content"

        # Mock existing file metadata
        mock_redis_client.hgetall.return_value = {
            "file_id": file_id,
            "filename": "test.txt",
            "object_key": f"sessions/{session_id}/uploads/{file_id}",
            "content_type": "text/plain",
        }

        result = await file_service.update_file_content(
            session_id=session_id,
            file_id=file_id,
            content=new_content,
        )

        assert result is True
        # Verify MinIO put_object was called
        mock_minio_client.put_object.assert_called_once()
        # Verify metadata was updated
        mock_redis_client.hset.assert_called()

    @pytest.mark.asyncio
    async def test_update_file_content_updates_metadata(
        self, file_service, mock_minio_client, mock_redis_client
    ):
        """Test that update_file_content updates file size metadata."""
        session_id = "test-session-123"
        file_id = "test-file-456"
        new_content = b"new content with some data"

        mock_redis_client.hgetall.return_value = {
            "file_id": file_id,
            "filename": "data.txt",
            "object_key": f"sessions/{session_id}/uploads/{file_id}",
            "content_type": "text/plain",
        }

        result = await file_service.update_file_content(
            session_id=session_id,
            file_id=file_id,
            content=new_content,
        )

        assert result is True

        # Check that hset was called with correct updates
        hset_call = mock_redis_client.hset.call_args
        mapping = hset_call.kwargs.get("mapping")
        assert mapping is not None
        assert mapping["size"] == len(new_content)

    @pytest.mark.asyncio
    async def test_update_file_content_file_not_found(
        self, file_service, mock_redis_client
    ):
        """Test graceful handling of missing file."""
        session_id = "test-session"
        file_id = "nonexistent-file"

        # Mock file not found
        mock_redis_client.hgetall.return_value = {}

        result = await file_service.update_file_content(
            session_id=session_id,
            file_id=file_id,
            content=b"content",
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_update_file_content_no_object_key(
        self, file_service, mock_redis_client
    ):
        """Test handling of metadata without object_key."""
        session_id = "test-session"
        file_id = "file-no-key"

        # Mock metadata without object_key
        mock_redis_client.hgetall.return_value = {
            "file_id": file_id,
            "filename": "test.txt",
            # object_key is missing
        }

        result = await file_service.update_file_content(
            session_id=session_id,
            file_id=file_id,
            content=b"content",
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_update_file_content_minio_error(
        self, file_service, mock_minio_client, mock_redis_client
    ):
        """Test handling of MinIO error during update."""
        session_id = "test-session"
        file_id = "file-id"

        mock_redis_client.hgetall.return_value = {
            "file_id": file_id,
            "filename": "test.txt",
            "object_key": f"sessions/{session_id}/uploads/{file_id}",
            "content_type": "text/plain",
        }

        # Mock MinIO error
        mock_minio_client.put_object.side_effect = Exception("MinIO connection error")

        result = await file_service.update_file_content(
            session_id=session_id,
            file_id=file_id,
            content=b"content",
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_update_file_content_preserves_content_type(
        self, file_service, mock_minio_client, mock_redis_client
    ):
        """Test that content_type is preserved from original metadata."""
        session_id = "test-session"
        file_id = "image-file"
        new_content = b"\x89PNG\r\n\x1a\n..."  # PNG bytes

        mock_redis_client.hgetall.return_value = {
            "file_id": file_id,
            "filename": "image.png",
            "object_key": f"sessions/{session_id}/uploads/{file_id}",
            "content_type": "image/png",
        }

        result = await file_service.update_file_content(
            session_id=session_id,
            file_id=file_id,
            content=new_content,
        )

        assert result is True
        # Verify put_object was called with preserved content_type
        put_call = mock_minio_client.put_object.call_args
        # The content_type should be "image/png" from the metadata
        assert "image/png" in str(put_call)

    @pytest.mark.asyncio
    async def test_update_file_content_only_updates_size(
        self, file_service, mock_minio_client, mock_redis_client
    ):
        """Test that update_file_content only updates size metadata."""
        session_id = "test-session"
        file_id = "file-id"

        mock_redis_client.hgetall.return_value = {
            "file_id": file_id,
            "filename": "test.txt",
            "object_key": f"sessions/{session_id}/uploads/{file_id}",
            "content_type": "text/plain",
        }

        result = await file_service.update_file_content(
            session_id=session_id,
            file_id=file_id,
            content=b"just content, no state",
        )

        assert result is True

        hset_call = mock_redis_client.hset.call_args
        mapping = hset_call.kwargs.get("mapping")
        assert mapping == {"size": len(b"just content, no state")}


class TestLinkedFiles:
    """Tests for linked-input alias behavior."""

    @pytest.mark.asyncio
    async def test_link_file_into_session_creates_read_only_alias(
        self, file_service, mock_redis_client
    ):
        """Linking should create a current-session alias to the source object."""
        mock_redis_client.smembers.return_value = set()
        mock_redis_client.hgetall.side_effect = [
            {
                "file_id": "source-file",
                "filename": "report.csv",
                "content_type": "text/csv",
                "object_key": "sessions/source-session/uploads/source-file",
                "session_id": "source-session",
                "created_at": datetime.utcnow().isoformat(),
                "size": "12",
                "path": "/report.csv",
                "type": "upload",
            }
        ]

        linked_file = await file_service.link_file_into_session(
            "target-session", "source-session", "source-file"
        )

        assert linked_file is not None
        assert linked_file.filename == "report.csv"
        hset_call = mock_redis_client.hset.call_args_list[0]
        metadata = hset_call.kwargs["mapping"]
        assert metadata["type"] == "linked_input"
        assert metadata["source_session_id"] == "source-session"
        assert metadata["source_file_id"] == "source-file"
        assert metadata["object_key"] == "sessions/source-session/uploads/source-file"
        assert metadata["is_read_only"] == "1"

    @pytest.mark.asyncio
    async def test_link_file_into_session_reuses_existing_alias(
        self, file_service, mock_redis_client
    ):
        """Repeated linking of the same source file should reuse the alias."""
        existing_created_at = datetime.utcnow().isoformat()
        mock_redis_client.smembers.return_value = {"linked-file"}
        mock_redis_client.hgetall.side_effect = [
            {
                "file_id": "source-file",
                "filename": "report.csv",
                "content_type": "text/csv",
                "object_key": "sessions/source/uploads/source-file",
                "session_id": "source-session",
                "created_at": datetime.utcnow().isoformat(),
                "size": "12",
                "path": "/report.csv",
                "type": "upload",
            },
            {
                "file_id": "linked-file",
                "filename": "report.csv",
                "content_type": "text/csv",
                "object_key": "sessions/source/uploads/source-file",
                "session_id": "target-session",
                "created_at": existing_created_at,
                "size": "12",
                "path": "/report.csv",
                "type": "linked_input",
                "source_session_id": "source-session",
                "source_file_id": "source-file",
                "is_read_only": "1",
            },
            {
                "file_id": "linked-file",
                "filename": "report.csv",
                "content_type": "text/csv",
                "object_key": "sessions/source/uploads/source-file",
                "session_id": "target-session",
                "created_at": existing_created_at,
                "size": "12",
                "path": "/report.csv",
                "type": "linked_input",
                "source_session_id": "source-session",
                "source_file_id": "source-file",
                "is_read_only": "1",
            },
        ]

        linked_file = await file_service.link_file_into_session(
            "target-session", "source-session", "source-file"
        )

        assert linked_file is not None
        assert linked_file.file_id == "linked-file"
        assert len(mock_redis_client.hset.call_args_list) == 0

    @pytest.mark.asyncio
    async def test_delete_linked_file_only_removes_metadata(
        self, file_service, mock_minio_client, mock_redis_client
    ):
        """Deleting a linked alias must not delete the shared object."""
        mock_redis_client.hgetall.return_value = {
            "file_id": "linked-file",
            "filename": "report.csv",
            "content_type": "text/csv",
            "object_key": "sessions/source/uploads/source-file",
            "session_id": "target-session",
            "created_at": datetime.utcnow().isoformat(),
            "size": "12",
            "path": "/report.csv",
            "type": "linked_input",
            "source_session_id": "source-session",
            "source_file_id": "source-file",
            "is_read_only": "1",
        }

        result = await file_service.delete_file("target-session", "linked-file")

        assert result is True
        mock_minio_client.remove_object.assert_not_called()
        mock_redis_client.delete.assert_called_once()
        assert mock_redis_client.srem.call_count == 2

    @pytest.mark.asyncio
    async def test_delete_source_file_keeps_object_when_aliases_exist(
        self, file_service, mock_minio_client, mock_redis_client
    ):
        """Deleting the source metadata must not delete a shared object still referenced by aliases."""
        mock_redis_client.hgetall.return_value = {
            "file_id": "source-file",
            "filename": "report.csv",
            "content_type": "text/csv",
            "object_key": "sessions/source/uploads/source-file",
            "session_id": "source-session",
            "created_at": datetime.utcnow().isoformat(),
            "size": "12",
            "path": "/report.csv",
            "type": "upload",
        }
        mock_redis_client.smembers.return_value = {"target-session:linked-file"}

        result = await file_service.delete_file("source-session", "source-file")

        assert result is True
        mock_minio_client.remove_object.assert_not_called()
        mock_redis_client.delete.assert_called_once()
