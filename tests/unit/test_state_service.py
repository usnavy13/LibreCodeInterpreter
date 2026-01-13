"""Unit tests for the StateService."""

import base64
import hashlib
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from src.services.state import StateService


@pytest.fixture
def mock_redis_client():
    """Mock Redis client."""
    client = AsyncMock()
    client.get = AsyncMock(return_value=None)
    client.setex = AsyncMock()
    client.delete = AsyncMock()
    client.strlen = AsyncMock(return_value=0)
    client.ttl = AsyncMock(return_value=-1)
    client.expire = AsyncMock()
    client.pipeline = MagicMock()
    return client


@pytest.fixture
def state_service(mock_redis_client):
    """Create StateService with mocked Redis."""
    with patch("src.services.state.redis_pool") as mock_pool:
        mock_pool.get_client.return_value = mock_redis_client
        service = StateService(redis_client=mock_redis_client)
        return service


class TestComputeHash:
    """Tests for hash computation."""

    def test_compute_hash_returns_sha256(self):
        """Test that compute_hash returns SHA256 hex digest."""
        raw_bytes = b"test data for hashing"
        expected = hashlib.sha256(raw_bytes).hexdigest()

        result = StateService.compute_hash(raw_bytes)

        assert result == expected

    def test_compute_hash_is_deterministic(self):
        """Test that same input produces same hash."""
        raw_bytes = b"reproducible test data"

        hash1 = StateService.compute_hash(raw_bytes)
        hash2 = StateService.compute_hash(raw_bytes)

        assert hash1 == hash2

    def test_compute_hash_different_for_different_input(self):
        """Test that different input produces different hash."""
        bytes1 = b"data version 1"
        bytes2 = b"data version 2"

        hash1 = StateService.compute_hash(bytes1)
        hash2 = StateService.compute_hash(bytes2)

        assert hash1 != hash2


class TestSaveState:
    """Tests for save_state method."""

    @pytest.mark.asyncio
    async def test_save_state_stores_hash_and_metadata(
        self, state_service, mock_redis_client
    ):
        """Test that save_state stores state, hash, and metadata."""
        session_id = "test-session-123"
        raw_bytes = b"\x02test state data"  # Version 2 prefix
        state_b64 = base64.b64encode(raw_bytes).decode("utf-8")

        # Setup mock pipeline
        mock_pipe = AsyncMock()
        mock_pipe.setex = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[True, True, True])
        mock_redis_client.pipeline.return_value = mock_pipe

        result = await state_service.save_state(session_id, state_b64)

        assert result is True
        # Verify pipeline was used with 3 setex calls (state, hash, meta)
        assert mock_pipe.setex.call_count == 3

    @pytest.mark.asyncio
    async def test_save_state_with_upload_marker(
        self, state_service, mock_redis_client
    ):
        """Test that from_upload=True sets upload marker."""
        session_id = "test-session-upload"
        raw_bytes = b"\x02uploaded state"
        state_b64 = base64.b64encode(raw_bytes).decode("utf-8")

        mock_pipe = AsyncMock()
        mock_pipe.setex = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[True, True, True, True])
        mock_redis_client.pipeline.return_value = mock_pipe

        result = await state_service.save_state(session_id, state_b64, from_upload=True)

        assert result is True
        # Verify 4 setex calls (state, hash, meta, marker)
        assert mock_pipe.setex.call_count == 4

    @pytest.mark.asyncio
    async def test_save_state_empty_returns_true(self, state_service):
        """Test that empty state returns True without saving."""
        result = await state_service.save_state("session", "")

        assert result is True


class TestGetStateRaw:
    """Tests for get_state_raw method."""

    @pytest.mark.asyncio
    async def test_get_state_raw_decodes_base64(self, state_service, mock_redis_client):
        """Test that get_state_raw returns decoded bytes."""
        session_id = "test-session"
        raw_bytes = b"\x02raw binary state data"
        state_b64 = base64.b64encode(raw_bytes).decode("utf-8")

        mock_redis_client.get.return_value = state_b64

        result = await state_service.get_state_raw(session_id)

        assert result == raw_bytes

    @pytest.mark.asyncio
    async def test_get_state_raw_returns_none_when_no_state(
        self, state_service, mock_redis_client
    ):
        """Test that get_state_raw returns None when no state exists."""
        mock_redis_client.get.return_value = None

        result = await state_service.get_state_raw("nonexistent")

        assert result is None


class TestSaveStateRaw:
    """Tests for save_state_raw method."""

    @pytest.mark.asyncio
    async def test_save_state_raw_encodes_to_base64(
        self, state_service, mock_redis_client
    ):
        """Test that save_state_raw encodes bytes to base64."""
        session_id = "test-session"
        raw_bytes = b"\x02raw data to save"

        mock_pipe = AsyncMock()
        mock_pipe.setex = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[True, True, True])
        mock_redis_client.pipeline.return_value = mock_pipe

        result = await state_service.save_state_raw(session_id, raw_bytes)

        assert result is True


class TestGetStateHash:
    """Tests for get_state_hash method."""

    @pytest.mark.asyncio
    async def test_get_state_hash_returns_string(
        self, state_service, mock_redis_client
    ):
        """Test that get_state_hash returns hash string."""
        expected_hash = "abc123def456"
        mock_redis_client.get.return_value = expected_hash.encode("utf-8")

        result = await state_service.get_state_hash("session-id")

        assert result == expected_hash

    @pytest.mark.asyncio
    async def test_get_state_hash_returns_none_when_missing(
        self, state_service, mock_redis_client
    ):
        """Test that get_state_hash returns None when no hash."""
        mock_redis_client.get.return_value = None

        result = await state_service.get_state_hash("session-id")

        assert result is None


class TestUploadMarker:
    """Tests for upload marker methods."""

    @pytest.mark.asyncio
    async def test_has_recent_upload_true_when_marker_exists(
        self, state_service, mock_redis_client
    ):
        """Test that has_recent_upload returns True when marker exists."""
        mock_redis_client.get.return_value = "1"

        result = await state_service.has_recent_upload("session-id")

        assert result is True

    @pytest.mark.asyncio
    async def test_has_recent_upload_false_when_no_marker(
        self, state_service, mock_redis_client
    ):
        """Test that has_recent_upload returns False when no marker."""
        mock_redis_client.get.return_value = None

        result = await state_service.has_recent_upload("session-id")

        assert result is False

    @pytest.mark.asyncio
    async def test_clear_upload_marker_deletes_key(
        self, state_service, mock_redis_client
    ):
        """Test that clear_upload_marker deletes the marker key."""
        await state_service.clear_upload_marker("session-id")

        mock_redis_client.delete.assert_called_once()


class TestDeleteState:
    """Tests for delete_state method."""

    @pytest.mark.asyncio
    async def test_delete_state_removes_all_keys(
        self, state_service, mock_redis_client
    ):
        """Test that delete_state removes state, hash, meta, and marker keys."""
        session_id = "session-to-delete"

        result = await state_service.delete_state(session_id)

        assert result is True
        # Verify delete was called with all 4 keys
        mock_redis_client.delete.assert_called_once()
        call_args = mock_redis_client.delete.call_args[0]
        assert len(call_args) == 4


class TestGetFullStateInfo:
    """Tests for get_full_state_info method."""

    @pytest.mark.asyncio
    async def test_get_full_state_info_returns_metadata(
        self, state_service, mock_redis_client
    ):
        """Test that get_full_state_info returns complete metadata."""
        session_id = "session-with-state"
        meta = {
            "size_bytes": 1024,
            "hash": "abc123",
            "created_at": "2025-12-21T10:00:00+00:00",
            "from_upload": False,
        }

        mock_pipe = AsyncMock()
        mock_pipe.strlen = MagicMock()
        mock_pipe.ttl = MagicMock()
        mock_pipe.get = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[1024, 3600, json.dumps(meta)])
        mock_redis_client.pipeline.return_value = mock_pipe

        result = await state_service.get_full_state_info(session_id)

        assert result is not None
        assert result["size_bytes"] == 1024
        assert result["hash"] == "abc123"
        assert result["expires_at"] is not None

    @pytest.mark.asyncio
    async def test_get_full_state_info_returns_none_when_no_state(
        self, state_service, mock_redis_client
    ):
        """Test that get_full_state_info returns None when no state."""
        mock_pipe = AsyncMock()
        mock_pipe.execute = AsyncMock(return_value=[0, -1, None])
        mock_redis_client.pipeline.return_value = mock_pipe

        result = await state_service.get_full_state_info("nonexistent")

        assert result is None
