"""Unit tests for the session service."""

import pytest
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import json

from src.services.session import SessionService
from src.models.session import Session, SessionCreate, SessionStatus


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    redis_mock = AsyncMock()

    # Mock pipeline
    pipeline_mock = AsyncMock()
    pipeline_mock.hset = MagicMock()
    pipeline_mock.expire = MagicMock()
    pipeline_mock.sadd = MagicMock()
    pipeline_mock.delete = MagicMock()
    pipeline_mock.srem = MagicMock()
    pipeline_mock.execute = AsyncMock(return_value=[True, True, True])
    pipeline_mock.reset = AsyncMock()

    # Make pipeline() return the pipeline mock when awaited
    redis_mock.pipeline = AsyncMock(return_value=pipeline_mock)
    return redis_mock


@pytest.fixture
def session_service(mock_redis):
    """Create a session service with mocked Redis."""
    return SessionService(redis_client=mock_redis)


@pytest.mark.asyncio
async def test_create_session(session_service, mock_redis):
    """Test session creation."""
    request = SessionCreate(metadata={"test": "value"})

    session = await session_service.create_session(request)

    assert session.session_id is not None
    assert session.status == SessionStatus.ACTIVE
    assert session.metadata == {"test": "value"}
    assert isinstance(session.created_at, datetime)
    assert isinstance(session.expires_at, datetime)
    assert session.expires_at > session.created_at

    # Verify Redis operations were called
    mock_redis.pipeline.assert_called_once()


@pytest.mark.asyncio
async def test_get_session_exists(session_service, mock_redis):
    """Test retrieving an existing session."""
    session_id = "test-session-id"
    session_data = {
        "session_id": session_id,
        "status": "active",
        "created_at": "2023-01-01T00:00:00",
        "last_activity": "2023-01-01T00:00:00",
        "expires_at": "2023-01-02T00:00:00",
        "files": "{}",
        "metadata": '{"test": "value"}',
        "working_directory": "/workspace",
    }

    mock_redis.hgetall.return_value = session_data
    mock_redis.hset = AsyncMock()

    session = await session_service.get_session(session_id)

    assert session is not None
    assert session.session_id == session_id
    assert session.status == SessionStatus.ACTIVE
    assert session.metadata == {"test": "value"}
    assert session.files == {}

    # Verify last activity was updated
    mock_redis.hset.assert_called_once()


@pytest.mark.asyncio
async def test_get_session_not_exists(session_service, mock_redis):
    """Test retrieving a non-existent session."""
    mock_redis.hgetall.return_value = {}

    session = await session_service.get_session("non-existent")

    assert session is None


@pytest.mark.asyncio
async def test_update_session(session_service, mock_redis):
    """Test updating a session."""
    session_id = "test-session-id"

    # Mock session exists
    mock_redis.exists.return_value = True
    mock_redis.hset = AsyncMock()

    # Mock get_session to return updated session
    updated_session_data = {
        "session_id": session_id,
        "status": "idle",
        "created_at": "2023-01-01T00:00:00",
        "last_activity": datetime.now(timezone.utc).isoformat(),
        "expires_at": "2023-01-02T00:00:00",
        "files": "{}",
        "metadata": "{}",
        "working_directory": "/workspace",
    }
    mock_redis.hgetall.return_value = updated_session_data

    session = await session_service.update_session(
        session_id, status=SessionStatus.IDLE
    )

    assert session is not None
    assert session.session_id == session_id

    # Verify Redis update was called
    mock_redis.hset.assert_called()


@pytest.mark.asyncio
async def test_update_session_not_exists(session_service, mock_redis):
    """Test updating a non-existent session."""
    mock_redis.exists.return_value = False

    session = await session_service.update_session(
        "non-existent", status=SessionStatus.IDLE
    )

    assert session is None


@pytest.mark.asyncio
async def test_delete_session(session_service, mock_redis):
    """Test deleting a session."""
    session_id = "test-session-id"

    # The pipeline mock is already set up in the fixture
    pipeline_mock = mock_redis.pipeline.return_value
    pipeline_mock.execute.return_value = [1, 1]  # Both operations successful

    result = await session_service.delete_session(session_id)

    assert result is True
    pipeline_mock.delete.assert_called_once()
    pipeline_mock.srem.assert_called_once()


@pytest.mark.asyncio
async def test_list_sessions(session_service, mock_redis):
    """Test listing sessions."""
    session_ids = ["session1", "session2", "session3"]
    mock_redis.smembers.return_value = session_ids

    # Mock get_session to return valid sessions
    def mock_hgetall(key):
        session_id = key.split(":")[-1]  # Extract session ID from key
        return {
            "session_id": session_id,
            "status": "active",
            "created_at": "2023-01-01T00:00:00+00:00",
            "last_activity": "2023-01-01T00:00:00+00:00",
            "expires_at": "2023-01-02T00:00:00+00:00",
            "files": "{}",
            "metadata": "{}",
            "working_directory": "/workspace",
        }

    mock_redis.hgetall.side_effect = mock_hgetall
    mock_redis.hset = AsyncMock()

    sessions = await session_service.list_sessions(limit=2)

    assert len(sessions) == 2  # Limited to 2
    assert all(isinstance(s, Session) for s in sessions)


@pytest.mark.asyncio
async def test_cleanup_expired_sessions(session_service, mock_redis):
    """Test cleaning up expired sessions."""
    session_ids = ["expired1", "expired2", "active1"]
    mock_redis.smembers.return_value = session_ids

    # Mock sessions - some expired, some active
    def mock_get_session(session_id):
        if session_id.startswith("expired"):
            return Session(
                session_id=session_id,
                status=SessionStatus.ACTIVE,
                created_at=datetime.now(timezone.utc) - timedelta(days=2),
                last_activity=datetime.now(timezone.utc) - timedelta(days=2),
                expires_at=datetime.now(timezone.utc) - timedelta(hours=1),  # Expired
            )
        else:
            return Session(
                session_id=session_id,
                status=SessionStatus.ACTIVE,
                created_at=datetime.now(timezone.utc),
                last_activity=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),  # Active
            )

    # The pipeline mock is already set up in the fixture
    pipeline_mock = mock_redis.pipeline.return_value
    pipeline_mock.execute.return_value = [1, 1]

    with patch.object(session_service, "get_session", side_effect=mock_get_session):
        cleaned_count = await session_service.cleanup_expired_sessions()

    assert cleaned_count == 2  # Two expired sessions cleaned


@pytest.mark.asyncio
async def test_session_key_generation(session_service):
    """Test session key generation."""
    session_id = "test-session"
    key = session_service._session_key(session_id)
    assert key == "sessions:test-session"


@pytest.mark.asyncio
async def test_generate_session_id(session_service):
    """Test session ID generation."""
    session_id = session_service._generate_session_id()
    assert isinstance(session_id, str)
    assert len(session_id) > 0

    # Generate another to ensure uniqueness
    session_id2 = session_service._generate_session_id()
    assert session_id != session_id2


@pytest.mark.asyncio
async def test_cleanup_task_lifecycle(session_service, mock_redis):
    """Test cleanup task start and stop."""
    # Start cleanup task
    await session_service.start_cleanup_task()
    assert session_service._cleanup_task is not None
    assert not session_service._cleanup_task.done()

    # Stop cleanup task
    await session_service.stop_cleanup_task()
    assert session_service._cleanup_task.done()


@pytest.mark.asyncio
async def test_create_session_with_entity_id(session_service, mock_redis):
    """Test session creation with entity_id."""
    request = SessionCreate(metadata={"entity_id": "test-entity", "test": "value"})

    session = await session_service.create_session(request)

    assert session.session_id is not None
    assert session.status == SessionStatus.ACTIVE
    assert session.metadata == {"entity_id": "test-entity", "test": "value"}

    # Verify Redis operations were called including entity grouping
    mock_redis.pipeline.assert_called_once()


@pytest.mark.asyncio
async def test_list_sessions_by_entity(session_service, mock_redis):
    """Test listing sessions by entity ID."""
    entity_id = "test-entity"
    session_ids = ["session1", "session2"]

    mock_redis.smembers.return_value = session_ids

    # Mock get_session to return valid sessions
    def mock_hgetall(key):
        session_id = key.split(":")[-1]
        return {
            "session_id": session_id,
            "status": "active",
            "created_at": "2023-01-01T00:00:00+00:00",
            "last_activity": "2023-01-01T00:00:00+00:00",
            "expires_at": "2023-01-02T00:00:00+00:00",
            "files": "{}",
            "metadata": '{"entity_id": "test-entity"}',
            "working_directory": "/workspace",
        }

    mock_redis.hgetall.side_effect = mock_hgetall
    mock_redis.hset = AsyncMock()

    sessions = await session_service.list_sessions_by_entity(entity_id)

    assert len(sessions) == 2
    assert all(isinstance(s, Session) for s in sessions)
    assert all(s.metadata.get("entity_id") == entity_id for s in sessions)


@pytest.mark.asyncio
async def test_validate_session_access_success(session_service, mock_redis):
    """Test successful session access validation."""
    session_id = "test-session"
    entity_id = "test-entity"

    session_data = {
        "session_id": session_id,
        "status": "active",
        "created_at": "2023-01-01T00:00:00+00:00",
        "last_activity": "2023-01-01T00:00:00+00:00",
        "expires_at": "2023-01-02T00:00:00+00:00",
        "files": "{}",
        "metadata": f'{{"entity_id": "{entity_id}"}}',
        "working_directory": "/workspace",
    }

    mock_redis.hgetall.return_value = session_data
    mock_redis.hset = AsyncMock()

    result = await session_service.validate_session_access(session_id, entity_id)

    assert result is True


@pytest.mark.asyncio
async def test_validate_session_access_wrong_entity(session_service, mock_redis):
    """Test session access validation with wrong entity ID."""
    session_id = "test-session"
    entity_id = "wrong-entity"

    session_data = {
        "session_id": session_id,
        "status": "active",
        "created_at": "2023-01-01T00:00:00+00:00",
        "last_activity": "2023-01-01T00:00:00+00:00",
        "expires_at": "2023-01-02T00:00:00+00:00",
        "files": "{}",
        "metadata": '{"entity_id": "test-entity"}',
        "working_directory": "/workspace",
    }

    mock_redis.hgetall.return_value = session_data
    mock_redis.hset = AsyncMock()

    result = await session_service.validate_session_access(session_id, entity_id)

    assert result is False


@pytest.mark.asyncio
async def test_validate_session_access_no_session(session_service, mock_redis):
    """Test session access validation when session doesn't exist."""
    mock_redis.hgetall.return_value = {}

    result = await session_service.validate_session_access(
        "non-existent", "test-entity"
    )

    assert result is False


@pytest.mark.asyncio
async def test_get_session_files_access_success(session_service, mock_redis):
    """Test successful session files access validation."""
    session_id = "test-session"
    entity_id = "test-entity"

    # Mock validate_session_access to return True
    with patch.object(session_service, "validate_session_access", return_value=True):
        # Mock list_sessions_by_entity to return sessions including the target session
        mock_sessions = [
            Session(
                session_id=session_id,
                status=SessionStatus.ACTIVE,
                created_at=datetime.now(timezone.utc),
                last_activity=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                metadata={"entity_id": entity_id},
            )
        ]
        with patch.object(
            session_service, "list_sessions_by_entity", return_value=mock_sessions
        ):
            result = await session_service.get_session_files_access(
                session_id, entity_id
            )

            assert result is True


@pytest.mark.asyncio
async def test_get_session_files_access_invalid_session(session_service, mock_redis):
    """Test session files access validation with invalid session."""
    # Mock validate_session_access to return False
    with patch.object(session_service, "validate_session_access", return_value=False):
        result = await session_service.get_session_files_access(
            "invalid-session", "test-entity"
        )

        assert result is False


@pytest.mark.asyncio
async def test_delete_session_with_entity_cleanup(session_service, mock_redis):
    """Test deleting a session with entity cleanup."""
    session_id = "test-session-id"
    entity_id = "test-entity"

    # Mock get_session to return a session with entity_id
    session_data = {
        "session_id": session_id,
        "status": "active",
        "created_at": "2023-01-01T00:00:00+00:00",
        "last_activity": "2023-01-01T00:00:00+00:00",
        "expires_at": "2023-01-02T00:00:00+00:00",
        "files": "{}",
        "metadata": f'{{"entity_id": "{entity_id}"}}',
        "working_directory": "/workspace",
    }
    mock_redis.hgetall.return_value = session_data
    mock_redis.hset = AsyncMock()

    # The pipeline mock is already set up in the fixture
    pipeline_mock = mock_redis.pipeline.return_value
    pipeline_mock.execute.return_value = [1, 1, 1]  # Three operations successful

    result = await session_service.delete_session(session_id)

    assert result is True
    pipeline_mock.delete.assert_called_once()
    pipeline_mock.srem.assert_called()  # Called twice - once for session index, once for entity


@pytest.mark.asyncio
async def test_close(session_service, mock_redis):
    """Test service cleanup."""
    await session_service.start_cleanup_task()
    await session_service.close()

    # Verify cleanup task was stopped and Redis connection closed
    mock_redis.close.assert_called_once()
