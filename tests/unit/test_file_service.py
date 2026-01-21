"""Unit tests for the FileService."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
import io

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
        """Test that update_file_content updates size, state_hash, execution_id."""
        session_id = "test-session-123"
        file_id = "test-file-456"
        new_content = b"new content with some data"
        state_hash = "abc123def456"
        execution_id = "exec-789"

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
            state_hash=state_hash,
            execution_id=execution_id,
        )

        assert result is True

        # Check that hset was called with correct updates
        hset_call = mock_redis_client.hset.call_args
        mapping = hset_call.kwargs.get("mapping")
        assert mapping is not None
        assert mapping["size"] == len(new_content)
        assert mapping["state_hash"] == state_hash
        assert mapping["execution_id"] == execution_id
        assert "last_used_at" in mapping

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
    async def test_update_file_content_optional_state_hash(
        self, file_service, mock_minio_client, mock_redis_client
    ):
        """Test that state_hash and execution_id are optional."""
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

        # Check that state_hash and execution_id are not in updates
        hset_call = mock_redis_client.hset.call_args
        mapping = hset_call.kwargs.get("mapping")
        assert "state_hash" not in mapping
        assert "execution_id" not in mapping
