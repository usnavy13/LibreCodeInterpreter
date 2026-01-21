"""Integration tests for new features: file ownership, args parameter, and state-file linking."""

import pytest
from datetime import datetime, timezone

from src.models.files import FileInfo


class TestFileInfoStateFields:
    """Tests for Issue 3: FileInfo model includes state fields."""

    def test_file_info_has_state_hash_field(self):
        """Test that FileInfo model includes state_hash field."""
        file_info = FileInfo(
            file_id="test-file-123",
            filename="test.txt",
            size=100,
            content_type="text/plain",
            created_at=datetime.now(timezone.utc),
            path="/outputs/test.txt",
            state_hash="abc123def456",
        )
        assert file_info.state_hash == "abc123def456"

    def test_file_info_has_execution_id_field(self):
        """Test that FileInfo model includes execution_id field."""
        file_info = FileInfo(
            file_id="test-file-123",
            filename="test.txt",
            size=100,
            content_type="text/plain",
            created_at=datetime.now(timezone.utc),
            path="/outputs/test.txt",
            execution_id="exec-789",
        )
        assert file_info.execution_id == "exec-789"

    def test_file_info_has_last_used_at_field(self):
        """Test that FileInfo model includes last_used_at field."""
        now = datetime.now(timezone.utc)
        file_info = FileInfo(
            file_id="test-file-123",
            filename="test.txt",
            size=100,
            content_type="text/plain",
            created_at=now,
            path="/outputs/test.txt",
            last_used_at=now,
        )
        assert file_info.last_used_at == now

    def test_file_info_state_fields_optional(self):
        """Test that state fields are optional (default to None)."""
        file_info = FileInfo(
            file_id="test-file-123",
            filename="test.txt",
            size=100,
            content_type="text/plain",
            created_at=datetime.now(timezone.utc),
            path="/outputs/test.txt",
        )
        assert file_info.state_hash is None
        assert file_info.execution_id is None
        assert file_info.last_used_at is None


class TestRequestFileRestoreState:
    """Tests for Issue 3: RequestFile model includes restore_state field."""

    def test_request_file_has_restore_state_field(self):
        """Test that RequestFile model includes restore_state field."""
        from src.models.exec import RequestFile

        file_ref = RequestFile(
            id="file-123",
            session_id="session-456",
            name="data.txt",
            restore_state=True,
        )
        assert file_ref.restore_state is True

    def test_request_file_restore_state_defaults_false(self):
        """Test that restore_state defaults to False."""
        from src.models.exec import RequestFile

        file_ref = RequestFile(
            id="file-123",
            session_id="session-456",
            name="data.txt",
        )
        assert file_ref.restore_state is False


class TestExecuteCodeRequestArgs:
    """Tests for Issue 2: ExecuteCodeRequest model includes args field."""

    def test_execute_code_request_has_args_field(self):
        """Test that ExecuteCodeRequest model includes args field."""
        from src.models.execution import ExecuteCodeRequest

        request = ExecuteCodeRequest(
            code="print('hello')",
            language="py",
            args=["arg1", "arg2"],
        )
        assert request.args == ["arg1", "arg2"]

    def test_execute_code_request_args_defaults_none(self):
        """Test that args defaults to None."""
        from src.models.execution import ExecuteCodeRequest

        request = ExecuteCodeRequest(
            code="print('hello')",
            language="py",
        )
        assert request.args is None


class TestNormalizeArgs:
    """Tests for args normalization in orchestrator."""

    def test_normalize_args_none(self):
        """Test that None args returns None."""
        from src.services.orchestrator import ExecutionOrchestrator

        orchestrator = ExecutionOrchestrator.__new__(ExecutionOrchestrator)
        result = orchestrator._normalize_args(None)
        assert result is None

    def test_normalize_args_string(self):
        """Test that string arg is converted to list."""
        from src.services.orchestrator import ExecutionOrchestrator

        orchestrator = ExecutionOrchestrator.__new__(ExecutionOrchestrator)
        result = orchestrator._normalize_args("single-arg")
        assert result == ["single-arg"]

    def test_normalize_args_empty_string(self):
        """Test that empty string returns None."""
        from src.services.orchestrator import ExecutionOrchestrator

        orchestrator = ExecutionOrchestrator.__new__(ExecutionOrchestrator)
        result = orchestrator._normalize_args("")
        assert result is None

    def test_normalize_args_list(self):
        """Test that list is passed through."""
        from src.services.orchestrator import ExecutionOrchestrator

        orchestrator = ExecutionOrchestrator.__new__(ExecutionOrchestrator)
        result = orchestrator._normalize_args(["arg1", "arg2"])
        assert result == ["arg1", "arg2"]

    def test_normalize_args_list_with_none(self):
        """Test that None values in list are filtered."""
        from src.services.orchestrator import ExecutionOrchestrator

        orchestrator = ExecutionOrchestrator.__new__(ExecutionOrchestrator)
        result = orchestrator._normalize_args(["arg1", None, "arg2"])
        assert result == ["arg1", "arg2"]

    def test_normalize_args_empty_list(self):
        """Test that empty list returns None."""
        from src.services.orchestrator import ExecutionOrchestrator

        orchestrator = ExecutionOrchestrator.__new__(ExecutionOrchestrator)
        result = orchestrator._normalize_args([])
        assert result is None

    def test_normalize_args_integer(self):
        """Test that integer is converted to string list."""
        from src.services.orchestrator import ExecutionOrchestrator

        orchestrator = ExecutionOrchestrator.__new__(ExecutionOrchestrator)
        result = orchestrator._normalize_args(42)
        assert result == ["42"]

    def test_normalize_args_with_spaces(self):
        """Test that args with spaces are preserved."""
        from src.services.orchestrator import ExecutionOrchestrator

        orchestrator = ExecutionOrchestrator.__new__(ExecutionOrchestrator)
        result = orchestrator._normalize_args(["arg with spaces", "another arg"])
        assert result == ["arg with spaces", "another arg"]


class TestStateServiceHashMethods:
    """Tests for hash-based state storage in StateService."""

    @pytest.mark.asyncio
    async def test_save_state_by_hash(self):
        """Test saving state by hash."""
        from src.services.state import StateService
        from unittest.mock import AsyncMock

        mock_redis = AsyncMock()
        mock_redis.setex = AsyncMock()

        service = StateService(redis_client=mock_redis)
        result = await service.save_state_by_hash(
            "abc123", "base64data", ttl_seconds=3600
        )

        assert result is True
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert "state:by_hash:abc123" in str(call_args)

    @pytest.mark.asyncio
    async def test_get_state_by_hash(self):
        """Test retrieving state by hash."""
        from src.services.state import StateService
        from unittest.mock import AsyncMock

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value="base64data")

        service = StateService(redis_client=mock_redis)
        result = await service.get_state_by_hash("abc123")

        assert result == "base64data"
        mock_redis.get.assert_called_once_with("state:by_hash:abc123")

    @pytest.mark.asyncio
    async def test_get_state_by_hash_not_found(self):
        """Test retrieving non-existent state by hash."""
        from src.services.state import StateService
        from unittest.mock import AsyncMock

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)

        service = StateService(redis_client=mock_redis)
        result = await service.get_state_by_hash("nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_extend_state_by_hash_ttl(self):
        """Test extending TTL of hash-indexed state."""
        from src.services.state import StateService
        from unittest.mock import AsyncMock

        mock_redis = AsyncMock()
        mock_redis.expire = AsyncMock(return_value=True)

        service = StateService(redis_client=mock_redis)
        result = await service.extend_state_by_hash_ttl("abc123", ttl_seconds=7200)

        assert result is True
        mock_redis.expire.assert_called_once()


class TestFileServiceStateHashMethods:
    """Tests for state hash methods in FileService."""

    @pytest.mark.asyncio
    async def test_get_file_state_hash(self):
        """Test getting file state hash."""
        from src.services.file import FileService
        from unittest.mock import AsyncMock, MagicMock

        mock_redis = AsyncMock()
        mock_redis.hget = AsyncMock(return_value="abc123def456")

        mock_minio = MagicMock()

        service = FileService.__new__(FileService)
        service.redis_client = mock_redis
        service.minio_client = mock_minio
        service.bucket_name = "test-bucket"

        result = await service.get_file_state_hash("session-123", "file-456")

        assert result == "abc123def456"

    @pytest.mark.asyncio
    async def test_update_file_state_hash(self):
        """Test updating file state hash."""
        from src.services.file import FileService
        from unittest.mock import AsyncMock, MagicMock

        mock_redis = AsyncMock()
        mock_redis.hset = AsyncMock()

        mock_minio = MagicMock()

        service = FileService.__new__(FileService)
        service.redis_client = mock_redis
        service.minio_client = mock_minio
        service.bucket_name = "test-bucket"

        result = await service.update_file_state_hash(
            "session-123", "file-456", "newhash789", execution_id="exec-abc"
        )

        assert result is True
        mock_redis.hset.assert_called_once()
        call_args = mock_redis.hset.call_args
        mapping = call_args[1]["mapping"]
        assert mapping["state_hash"] == "newhash789"
        assert mapping["execution_id"] == "exec-abc"
        assert "last_used_at" in mapping


class TestExecRequestArgsField:
    """Tests for args field in ExecRequest model."""

    def test_exec_request_accepts_args_list(self):
        """Test that ExecRequest accepts args as a list."""
        from src.models.exec import ExecRequest

        request = ExecRequest(
            code="print('hello')",
            lang="py",
            args=["arg1", "arg2"],
        )
        assert request.args == ["arg1", "arg2"]

    def test_exec_request_accepts_args_string(self):
        """Test that ExecRequest accepts args as a string."""
        from src.models.exec import ExecRequest

        request = ExecRequest(
            code="print('hello')",
            lang="py",
            args="single-arg",
        )
        # args field in ExecRequest is Any type, so it accepts any JSON value
        assert request.args == "single-arg"

    def test_exec_request_args_defaults_none(self):
        """Test that args defaults to None in ExecRequest."""
        from src.models.exec import ExecRequest

        request = ExecRequest(
            code="print('hello')",
            lang="py",
        )
        assert request.args is None


class TestUploadedFileStateRestoration:
    """Tests for uploaded file state restoration behavior.

    Uploaded files should share the same behavior as generated files:
    - After first use in execution, they get a state_hash
    - On subsequent use with restore_state=true, that state is restored
    """

    def test_uploaded_file_no_initial_state_hash(self):
        """Test that uploaded file has no state_hash initially."""
        file_info = FileInfo(
            file_id="uploaded-file-123",
            filename="data.csv",
            size=1024,
            content_type="text/csv",
            created_at=datetime.now(timezone.utc),
            path="/data.csv",
            # No state_hash, execution_id, or last_used_at
        )
        assert file_info.state_hash is None
        assert file_info.execution_id is None
        assert file_info.last_used_at is None

    def test_uploaded_file_gets_state_hash_after_use(self):
        """Test that uploaded file gets state_hash after being used in execution."""
        now = datetime.now(timezone.utc)

        # Simulate file before use
        file_before = FileInfo(
            file_id="uploaded-file-123",
            filename="data.csv",
            size=1024,
            content_type="text/csv",
            created_at=now,
            path="/data.csv",
        )
        assert file_before.state_hash is None

        # Simulate file after use (update_file_state_hash was called)
        file_after = FileInfo(
            file_id="uploaded-file-123",
            filename="data.csv",
            size=1024,
            content_type="text/csv",
            created_at=now,
            path="/data.csv",
            state_hash="abc123def456",
            execution_id="exec-789",
            last_used_at=now,
        )
        assert file_after.state_hash == "abc123def456"
        assert file_after.execution_id == "exec-789"
        assert file_after.last_used_at == now

    @pytest.mark.asyncio
    async def test_update_file_state_hash_works_for_uploaded_files(self):
        """Test that update_file_state_hash works on uploaded files."""
        from src.services.file import FileService
        from unittest.mock import AsyncMock, MagicMock

        mock_redis = AsyncMock()
        mock_redis.hset = AsyncMock()

        mock_minio = MagicMock()

        service = FileService.__new__(FileService)
        service.redis_client = mock_redis
        service.minio_client = mock_minio
        service.bucket_name = "test-bucket"

        # Call update_file_state_hash (simulating what happens after execution)
        result = await service.update_file_state_hash(
            session_id="session-123",
            file_id="uploaded-file-456",  # This is an uploaded file
            state_hash="statehash789",
            execution_id="exec-abc",
        )

        assert result is True
        mock_redis.hset.assert_called_once()

        # Verify the updates include all state fields
        call_args = mock_redis.hset.call_args
        mapping = call_args[1]["mapping"]
        assert mapping["state_hash"] == "statehash789"
        assert mapping["execution_id"] == "exec-abc"
        assert "last_used_at" in mapping

    def test_restore_state_flag_works_with_state_hash(self):
        """Test that RequestFile with restore_state=True works when file has state_hash."""
        from src.models.exec import RequestFile

        # Uploaded file reference with restore_state flag
        file_ref = RequestFile(
            id="uploaded-file-123",
            session_id="session-456",
            name="data.csv",
            restore_state=True,  # Request state restoration
        )
        assert file_ref.restore_state is True

    def test_restore_state_requires_state_hash_to_be_set(self):
        """Test that state restoration requires file to have state_hash.

        This documents expected behavior: if an uploaded file hasn't been used
        yet (no state_hash), restore_state=True is effectively ignored until
        the file is used in an execution.
        """
        # File with no state_hash (never used in execution)
        file_info_no_state = FileInfo(
            file_id="uploaded-file-123",
            filename="data.csv",
            size=1024,
            content_type="text/csv",
            created_at=datetime.now(timezone.utc),
            path="/data.csv",
        )

        # The mount logic checks: file_info.state_hash is truthy
        # For uploaded files that haven't been used, this will be None/False
        can_restore = bool(file_info_no_state.state_hash)
        assert can_restore is False

        # After first use, file has state_hash
        file_info_with_state = FileInfo(
            file_id="uploaded-file-123",
            filename="data.csv",
            size=1024,
            content_type="text/csv",
            created_at=datetime.now(timezone.utc),
            path="/data.csv",
            state_hash="abc123def456",
        )

        can_restore_now = bool(file_info_with_state.state_hash)
        assert can_restore_now is True
