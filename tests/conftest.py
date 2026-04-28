import os
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import redis.asyncio as redis
from minio import Minio

# Set test environment before importing config
# These match the docker-compose infrastructure settings
# Use setdefault to allow environment variables to override defaults
os.environ.setdefault("API_KEY", "test-api-key-for-testing-12345")
os.environ.setdefault("REDIS_HOST", "localhost")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ.setdefault("MINIO_ENDPOINT", "localhost:9000")
os.environ.setdefault("MINIO_ACCESS_KEY", "minioadmin")
os.environ.setdefault("MINIO_SECRET_KEY", "minioadmin")
os.environ.setdefault("MINIO_SECURE", "false")

from src.services.session import SessionService
from src.services.execution import CodeExecutionService
from src.services.file import FileService


@pytest.fixture
def mock_redis():
    """Mock Redis client for testing."""
    mock_client = AsyncMock(spec=redis.Redis)

    # Mock common Redis operations
    mock_client.hset = AsyncMock(return_value=1)
    mock_client.hgetall = AsyncMock(return_value={})
    mock_client.expire = AsyncMock(return_value=True)
    mock_client.delete = AsyncMock(return_value=1)
    mock_client.exists = AsyncMock(return_value=True)
    mock_client.sadd = AsyncMock(return_value=1)
    mock_client.srem = AsyncMock(return_value=1)
    mock_client.smembers = AsyncMock(return_value=set())
    mock_client.incr = AsyncMock(return_value=1)
    mock_client.get = AsyncMock(return_value=None)
    mock_client.setex = AsyncMock(return_value=True)
    mock_client.ping = AsyncMock(return_value=True)
    mock_client.close = AsyncMock()
    mock_client.scan_iter = AsyncMock(return_value=iter([]))

    return mock_client


@pytest.fixture
def mock_minio():
    """Mock MinIO client for testing."""
    mock_client = MagicMock(spec=Minio)

    # Mock common MinIO operations
    mock_client.bucket_exists.return_value = True
    mock_client.make_bucket.return_value = None
    mock_client.presigned_put_object.return_value = "https://example.com/upload"
    mock_client.presigned_get_object.return_value = "https://example.com/download"
    mock_client.stat_object.return_value = MagicMock(size=1024)
    mock_client.put_object.return_value = None
    mock_client.get_object.return_value = MagicMock()
    mock_client.remove_object.return_value = None

    return mock_client


@pytest.fixture
def mock_sandbox_manager():
    """Mock SandboxManager for testing."""
    from src.services.sandbox.nsjail import SandboxInfo

    manager = AsyncMock()
    manager.is_available.return_value = True
    manager.get_initialization_error.return_value = None

    # Create a mock SandboxInfo
    mock_sandbox = SandboxInfo(
        sandbox_id="test-sandbox-123",
        sandbox_dir=Path("/tmp/test-sandbox"),
        data_dir=Path("/tmp/test-sandbox/data"),
        language="py",
        session_id="test-session",
        created_at=datetime.utcnow(),
        repl_mode=False,
    )

    manager.create_sandbox.return_value = mock_sandbox
    manager.destroy_sandbox.return_value = True
    manager.copy_content_to_sandbox.return_value = True
    manager.get_file_content_from_sandbox.return_value = b"test content"
    manager.execute_command.return_value = (0, "output", "")
    manager.get_user_id_for_language.return_value = 1001
    manager.close.return_value = None

    return manager


@pytest.fixture
async def session_service(mock_redis):
    """Create SessionService instance with mocked Redis."""
    service = SessionService(redis_client=mock_redis)
    yield service
    await service.close()


@pytest.fixture
def execution_service(mock_sandbox_manager):
    """Create CodeExecutionService instance with mocked dependencies."""
    with patch(
        "src.services.execution.runner.SandboxManager",
        return_value=mock_sandbox_manager,
    ):
        service = CodeExecutionService()
        yield service


@pytest.fixture
def file_service(mock_minio, mock_redis):
    """Create FileService instance with mocked dependencies."""
    with patch("src.services.file.Minio", return_value=mock_minio), patch(
        "src.services.file.redis.Redis", return_value=mock_redis
    ):
        service = FileService()
        yield service


@pytest.fixture
def mock_settings():
    """Mock settings for testing."""
    with patch("src.config.settings") as mock_settings:
        mock_settings.redis_host = "localhost"
        mock_settings.redis_port = 6379
        mock_settings.redis_password = None
        mock_settings.redis_db = 0
        mock_settings.redis_url = None
        mock_settings.session_ttl_hours = 24
        mock_settings.session_cleanup_interval_minutes = 60
        mock_settings.minio_endpoint = "localhost:9000"
        mock_settings.minio_access_key = "test_key"
        mock_settings.minio_secret_key = "test_secret"
        mock_settings.minio_secure = False
        mock_settings.minio_bucket = "test-bucket"
        mock_settings.api_key = "test-api-key-12345"
        mock_settings.max_execution_time = 30
        mock_settings.max_file_size_mb = 10
        mock_settings.max_output_files = 10

        # Add helper methods
        mock_settings.get_session_ttl_minutes = (
            lambda: mock_settings.session_ttl_hours * 60
        )

        yield mock_settings


# ============================================================================
# Integration Test Fixtures
# ============================================================================


@pytest.fixture
def client():
    """Create FastAPI test client for integration tests."""
    from fastapi.testclient import TestClient
    from src.main import app

    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Provide authentication headers for integration tests."""
    return {"x-api-key": "test-api-key-for-testing-12345"}


def pytest_collection_modifyitems(config, items):
    """Apply shared markers based on the suite layer."""
    contract_only_files = (
        "tests/integration/test_api_contracts.py",
        "tests/integration/test_librechat_compat.py",
        "tests/integration/test_programmatic_api.py",
        "tests/integration/test_state_api.py",
    )
    slow_files = (
        "tests/functional/test_client_replay.py",
        "tests/functional/test_concurrent_file_exec.py",
        "tests/functional/test_generated_artifacts.py",
        "tests/functional/test_mounted_file_edits.py",
        "tests/functional/test_timing.py",
    )
    client_replay_files = ("tests/functional/test_client_replay.py",)

    for item in items:
        path = Path(str(item.fspath)).as_posix()
        if "/tests/functional/" in path or path.startswith("tests/functional/"):
            item.add_marker(pytest.mark.live_api)
        if path.endswith(contract_only_files):
            item.add_marker(pytest.mark.contract_only)
        if path.endswith(slow_files):
            item.add_marker(pytest.mark.slow)
        if path.endswith(client_replay_files):
            item.add_marker(pytest.mark.client_replay)
