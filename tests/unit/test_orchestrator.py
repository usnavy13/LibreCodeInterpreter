"""Unit tests for the execution orchestrator."""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock

from src.services.orchestrator import ExecutionOrchestrator, ExecutionContext
from src.models.exec import ExecRequest, FileRef
from src.models.files import FileInfo
from src.models.session import Session, SessionStatus


@pytest.fixture
def mock_session_service():
    """Create a mock session service."""
    service = AsyncMock()
    service.get_session = AsyncMock(
        return_value=Session(
            session_id="test-session-123",
            status=SessionStatus.ACTIVE,
            created_at=datetime.now(),
            last_activity=datetime.now(),
            expires_at=datetime.now(),
            files={},
            metadata={},
            working_directory="/workspace",
        )
    )
    service.create_session = AsyncMock(
        return_value=Session(
            session_id="new-session-456",
            status=SessionStatus.ACTIVE,
            created_at=datetime.now(),
            last_activity=datetime.now(),
            expires_at=datetime.now(),
            files={},
            metadata={},
            working_directory="/workspace",
        )
    )
    service.list_sessions_by_entity = AsyncMock(return_value=[])
    return service


@pytest.fixture
def mock_file_service():
    """Create a mock file service."""
    service = AsyncMock()
    service.get_file_info = AsyncMock(return_value=None)
    service.list_files = AsyncMock(return_value=[])
    service.get_file_metadata = AsyncMock(return_value=None)
    service.link_file_into_session = AsyncMock(return_value=None)
    return service


@pytest.fixture
def mock_execution_service():
    """Create a mock execution service."""
    service = AsyncMock()
    return service


@pytest.fixture
def orchestrator(mock_session_service, mock_file_service, mock_execution_service):
    """Create an orchestrator with mocked services."""
    return ExecutionOrchestrator(
        session_service=mock_session_service,
        file_service=mock_file_service,
        execution_service=mock_execution_service,
    )


class TestMountFiles:
    """Tests for file mounting behavior."""

    @pytest.mark.asyncio
    async def test_mount_files_no_files_no_session(self, orchestrator):
        """When no files and no session_id, should return empty list."""
        request = ExecRequest(code="print('hello')", lang="py")
        ctx = ExecutionContext(request=request, request_id="test-123")

        result = await orchestrator._mount_files(ctx)

        assert result == []

    @pytest.mark.asyncio
    async def test_mount_files_with_session_id_auto_mounts(
        self, orchestrator, mock_file_service
    ):
        """When session_id exists but no explicit files, should auto-mount all session files."""
        # Setup: session has two files (one uploaded, one generated)
        mock_file_service.list_files = AsyncMock(
            return_value=[
                FileInfo(
                    file_id="file-1",
                    filename="data.csv",
                    size=100,
                    content_type="text/csv",
                    created_at=datetime.now(),
                    path="/mnt/data/data.csv",
                ),
                FileInfo(
                    file_id="file-2",
                    filename="output.png",
                    size=500,
                    content_type="image/png",
                    created_at=datetime.now(),
                    path="/mnt/data/output.png",
                ),
            ]
        )
        mock_file_service.get_file_metadata = AsyncMock(
            side_effect=[
                {"type": "upload"},
                {"type": "output"},
            ]
        )

        request = ExecRequest(code="print('hello')", lang="py")
        ctx = ExecutionContext(
            request=request,
            request_id="test-123",
            session_id="test-session-123",  # Session exists
        )

        result = await orchestrator._mount_files(ctx)

        # Verify both files were auto-mounted
        assert len(result) == 2
        assert result[0]["file_id"] == "file-1"
        assert result[0]["filename"] == "data.csv"
        assert result[0]["session_id"] == "test-session-123"
        assert result[1]["file_id"] == "file-2"
        assert result[1]["filename"] == "output.png"
        assert result[1]["session_id"] == "test-session-123"

    @pytest.mark.asyncio
    async def test_mount_files_empty_session(self, orchestrator, mock_file_service):
        """When session_id exists but session has no files, should return empty list."""
        mock_file_service.list_files = AsyncMock(return_value=[])

        request = ExecRequest(code="print('hello')", lang="py")
        ctx = ExecutionContext(
            request=request,
            request_id="test-123",
            session_id="test-session-123",
        )

        result = await orchestrator._mount_files(ctx)

        assert result == []

    @pytest.mark.asyncio
    async def test_mount_files_explicit_files_takes_precedence(
        self, orchestrator, mock_file_service
    ):
        """Explicit files should win over current-session files with the same name."""
        from src.models.exec import RequestFile

        mock_file_service.get_file_info = AsyncMock(
            return_value=FileInfo(
                file_id="explicit-file",
                filename="report.csv",
                size=50,
                content_type="text/plain",
                created_at=datetime.now(),
                path="/mnt/data/report.csv",
            )
        )
        mock_file_service.list_files = AsyncMock(
            return_value=[
                FileInfo(
                    file_id="native-file",
                    filename="report.csv",
                    size=60,
                    content_type="text/csv",
                    created_at=datetime.now(),
                    path="/mnt/data/report.csv",
                ),
            ]
        )
        mock_file_service.get_file_metadata = AsyncMock(return_value={"type": "upload"})

        request = ExecRequest(
            code="print('hello')",
            lang="py",
            files=[
                RequestFile(
                    id="explicit-file", session_id="other-session", name="report.csv"
                ),
            ],
        )
        ctx = ExecutionContext(
            request=request,
            request_id="test-123",
            session_id="test-session-123",
        )

        result = await orchestrator._mount_files(ctx)

        # Verify only the explicit file wins for the mounted filename
        assert len(result) == 1
        assert result[0]["file_id"] == "explicit-file"
        assert result[0]["filename"] == "report.csv"
        assert result[0]["session_id"] == "other-session"  # Uses file's session_id

        # Verify cross-session explicit files are linked into the current session
        mock_file_service.get_file_info.assert_called_once()
        mock_file_service.link_file_into_session.assert_called_once_with(
            "test-session-123",
            "other-session",
            "explicit-file",
        )


class TestAutoMountSessionFiles:
    """Tests specifically for the auto-mount behavior."""

    @pytest.mark.asyncio
    async def test_auto_mount_deduplicates_files(self, orchestrator, mock_file_service):
        """Auto-mount should skip duplicate files."""
        mock_file_service.list_files = AsyncMock(
            return_value=[
                FileInfo(
                    file_id="file-1",
                    filename="data.csv",
                    size=100,
                    content_type="text/csv",
                    created_at=datetime.now(),
                    path="/mnt/data/data.csv",
                ),
            ]
        )
        mock_file_service.get_file_metadata = AsyncMock(return_value={"type": "upload"})

        request = ExecRequest(code="print('hello')", lang="py")
        ctx = ExecutionContext(
            request=request,
            request_id="test-123",
            session_id="test-session-123",
        )

        result = await orchestrator._auto_mount_session_files(ctx)

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_auto_mount_returns_session_files(
        self, orchestrator, mock_file_service
    ):
        """Auto-mount should return session files ready for mounting."""
        mock_file_service.list_files = AsyncMock(
            return_value=[
                FileInfo(
                    file_id="file-1",
                    filename="data.csv",
                    size=100,
                    content_type="text/csv",
                    created_at=datetime.now(),
                    path="/mnt/data/data.csv",
                ),
            ]
        )
        mock_file_service.get_file_metadata = AsyncMock(return_value={"type": "upload"})

        request = ExecRequest(code="print('hello')", lang="py")
        ctx = ExecutionContext(
            request=request,
            request_id="test-123",
            session_id="test-session-123",
        )

        result = await orchestrator._auto_mount_session_files(ctx)

        assert result == [
            {
                "file_id": "file-1",
                "filename": "data.csv",
                "path": "/mnt/data/data.csv",
                "size": 100,
                "session_id": "test-session-123",
                "is_linked_input": False,
                "is_read_only": False,
            }
        ]

    @pytest.mark.asyncio
    async def test_auto_mount_marks_linked_input_aliases(
        self, orchestrator, mock_file_service
    ):
        """Auto-mount should flag linked-input aliases for precedence and read-only handling."""
        mock_file_service.list_files = AsyncMock(
            return_value=[
                FileInfo(
                    file_id="linked-file",
                    filename="report.csv",
                    size=100,
                    content_type="text/csv",
                    created_at=datetime.now(),
                    path="/report.csv",
                ),
            ]
        )
        mock_file_service.get_file_metadata = AsyncMock(
            return_value={"type": "linked_input", "is_read_only": "1"}
        )

        request = ExecRequest(code="print('hello')", lang="py")
        ctx = ExecutionContext(
            request=request,
            request_id="test-123",
            session_id="test-session-123",
        )

        result = await orchestrator._auto_mount_session_files(ctx)

        assert result[0]["is_linked_input"] is True


class TestFileRefResponse:
    """Tests for FileRef response with session_id."""

    def test_file_ref_includes_session_id(self):
        """FileRef should include session_id field."""
        ref = FileRef(id="file-1", name="output.png", session_id="session-123")

        assert ref.id == "file-1"
        assert ref.name == "output.png"
        assert ref.session_id == "session-123"

    def test_file_ref_session_id_optional(self):
        """FileRef session_id should be optional for backward compatibility."""
        ref = FileRef(id="file-1", name="output.png")

        assert ref.id == "file-1"
        assert ref.name == "output.png"
        assert ref.session_id is None


class TestAgentFileSessionIsolation:
    """Tests for session isolation when files reference shared agent sessions.

    When multiple users share an agent with attached files, the file references
    carry the upload session_id. The orchestrator must NOT blindly reuse that
    session, as it would leak state between users. It should only reuse a
    file-referenced session if user_id matches.
    """

    @pytest.mark.asyncio
    async def test_agent_file_does_not_reuse_upload_session(
        self, orchestrator, mock_session_service
    ):
        """Files reference an upload session (no user_id). New session should be created."""
        from src.models.exec import RequestFile

        # Upload session S1 has no user_id in metadata (agent upload sessions don't)
        upload_session = Session(
            session_id="upload-session-S1",
            status=SessionStatus.ACTIVE,
            created_at=datetime.now(),
            last_activity=datetime.now(),
            expires_at=datetime.now(),
            files={},
            metadata={},  # No user_id
            working_directory="/workspace",
        )
        mock_session_service.get_session = AsyncMock(return_value=upload_session)

        request = ExecRequest(
            code="print('hello')",
            lang="py",
            user_id="userA",
            files=[
                RequestFile(
                    id="file-1", session_id="upload-session-S1", name="data.csv"
                ),
            ],
        )
        ctx = ExecutionContext(request=request, request_id="test-isolation-1")

        session_id = await orchestrator._get_or_create_session(ctx)

        # Should NOT reuse S1 (no user_id in session metadata)
        assert session_id == "new-session-456"
        mock_session_service.create_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_same_user_reuses_own_session(
        self, orchestrator, mock_session_service
    ):
        """Files reference a session created by the same user. Should reuse it."""
        from src.models.exec import RequestFile

        # Session S2 has user_id: "userA" in metadata
        user_session = Session(
            session_id="user-session-S2",
            status=SessionStatus.ACTIVE,
            created_at=datetime.now(),
            last_activity=datetime.now(),
            expires_at=datetime.now(),
            files={},
            metadata={"user_id": "userA"},
            working_directory="/workspace",
        )
        mock_session_service.get_session = AsyncMock(return_value=user_session)

        request = ExecRequest(
            code="print('hello')",
            lang="py",
            user_id="userA",
            files=[
                RequestFile(id="file-1", session_id="user-session-S2", name="data.csv"),
            ],
        )
        ctx = ExecutionContext(request=request, request_id="test-isolation-2")

        session_id = await orchestrator._get_or_create_session(ctx)

        # Should reuse S2 (same user_id)
        assert session_id == "user-session-S2"
        mock_session_service.create_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_different_user_does_not_reuse_session(
        self, orchestrator, mock_session_service
    ):
        """Files reference a session owned by a different user. New session should be created."""
        from src.models.exec import RequestFile

        # Session S2 has user_id: "userA"
        user_a_session = Session(
            session_id="user-session-S2",
            status=SessionStatus.ACTIVE,
            created_at=datetime.now(),
            last_activity=datetime.now(),
            expires_at=datetime.now(),
            files={},
            metadata={"user_id": "userA"},
            working_directory="/workspace",
        )
        mock_session_service.get_session = AsyncMock(return_value=user_a_session)

        request = ExecRequest(
            code="print('hello')",
            lang="py",
            user_id="userB",  # Different user
            files=[
                RequestFile(id="file-1", session_id="user-session-S2", name="data.csv"),
            ],
        )
        ctx = ExecutionContext(request=request, request_id="test-isolation-3")

        session_id = await orchestrator._get_or_create_session(ctx)

        # Should NOT reuse S2 (different user_id)
        assert session_id == "new-session-456"
        mock_session_service.create_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_user_id_creates_new_session(
        self, orchestrator, mock_session_service
    ):
        """Request without user_id should create a new session (no ownership check possible)."""
        from src.models.exec import RequestFile

        upload_session = Session(
            session_id="upload-session-S1",
            status=SessionStatus.ACTIVE,
            created_at=datetime.now(),
            last_activity=datetime.now(),
            expires_at=datetime.now(),
            files={},
            metadata={},
            working_directory="/workspace",
        )
        mock_session_service.get_session = AsyncMock(return_value=upload_session)

        request = ExecRequest(
            code="print('hello')",
            lang="py",
            # No user_id
            files=[
                RequestFile(
                    id="file-1", session_id="upload-session-S1", name="data.csv"
                ),
            ],
        )
        ctx = ExecutionContext(request=request, request_id="test-isolation-4")

        session_id = await orchestrator._get_or_create_session(ctx)

        # Should create new session (priority 2 requires request.user_id)
        assert session_id == "new-session-456"
        mock_session_service.create_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_entity_id_not_fallback_to_user_id(
        self, orchestrator, mock_session_service
    ):
        """user_id should NOT be used as fallback for entity_id in session lookup."""
        request = ExecRequest(
            code="print('hello')",
            lang="py",
            user_id="userA",
            # No entity_id
        )
        ctx = ExecutionContext(request=request, request_id="test-isolation-5")

        session_id = await orchestrator._get_or_create_session(ctx)

        # list_sessions_by_entity should NOT be called (no entity_id)
        mock_session_service.list_sessions_by_entity.assert_not_called()
        # Should create a new session
        assert session_id == "new-session-456"


class TestExplicitFileMounting:
    """Tests for explicit file mounting behavior."""

    @pytest.mark.asyncio
    async def test_explicit_mount_files(self, orchestrator, mock_file_service):
        """Explicit mount should mount requested files."""
        from src.models.exec import RequestFile

        mock_file_service.get_file_info = AsyncMock(
            return_value=FileInfo(
                file_id="file-1",
                filename="data.csv",
                size=100,
                content_type="text/csv",
                created_at=datetime.now(),
                path="/mnt/data/data.csv",
            )
        )

        request = ExecRequest(
            code="print('hello')",
            lang="py",
            files=[
                RequestFile(
                    id="file-1",
                    session_id="test-session",
                    name="data.csv",
                ),
            ],
        )
        ctx = ExecutionContext(
            request=request,
            request_id="test-123",
            session_id="test-session",
        )

        result = await orchestrator._mount_explicit_files(ctx)

        assert len(result) == 1
        assert result[0]["file_id"] == "file-1"
        assert result[0]["filename"] == "data.csv"

    @pytest.mark.asyncio
    async def test_explicit_mount_fallback_to_name_lookup(
        self, orchestrator, mock_file_service
    ):
        """Explicit mount should fallback to name lookup if ID not found."""
        from src.models.exec import RequestFile

        # First call returns None (ID not found), second returns file list
        mock_file_service.get_file_info = AsyncMock(return_value=None)
        mock_file_service.list_files = AsyncMock(
            return_value=[
                FileInfo(
                    file_id="actual-file-id",
                    filename="data.csv",
                    size=100,
                    content_type="text/csv",
                    created_at=datetime.now(),
                    path="/mnt/data/data.csv",
                ),
            ]
        )

        request = ExecRequest(
            code="print('hello')",
            lang="py",
            files=[
                RequestFile(
                    id="wrong-id",
                    session_id="test-session",
                    name="data.csv",
                ),
            ],
        )
        ctx = ExecutionContext(
            request=request,
            request_id="test-123",
            session_id="test-session",
        )

        result = await orchestrator._mount_explicit_files(ctx)

        # Verify fallback found the file by name
        assert len(result) == 1
        assert result[0]["file_id"] == "actual-file-id"
        assert result[0]["filename"] == "data.csv"

    @pytest.mark.asyncio
    async def test_explicit_mount_skips_not_found_files(
        self, orchestrator, mock_file_service
    ):
        """Explicit mount should skip files that can't be found."""
        from src.models.exec import RequestFile

        mock_file_service.get_file_info = AsyncMock(return_value=None)
        mock_file_service.list_files = AsyncMock(return_value=[])

        request = ExecRequest(
            code="print('hello')",
            lang="py",
            files=[
                RequestFile(
                    id="missing-file",
                    session_id="test-session",
                    name="missing.txt",
                ),
            ],
        )
        ctx = ExecutionContext(
            request=request,
            request_id="test-123",
            session_id="test-session",
        )

        result = await orchestrator._mount_explicit_files(ctx)

        assert len(result) == 0


class TestExecuteCodeTimeout:
    """Per-request timeout (ms) → execution timeout (s), clamped to server max.

    Implementation lives in `_execute_code` at orchestrator.py:661+. We patch
    the execution service to capture the constructed `ExecuteCodeRequest` and
    assert on its `timeout` (seconds)."""

    @pytest.mark.asyncio
    async def test_timeout_ms_to_seconds_with_ceil(self, orchestrator):
        from types import SimpleNamespace
        from src.models.execution import CodeExecution, ExecutionStatus
        from src.models.exec import ExecRequest

        captured = {}

        async def _capture(session_id, exec_request, mounted_files, **kwargs):
            captured["request"] = exec_request
            return (
                CodeExecution(
                    execution_id="x",
                    session_id="s",
                    code="",
                    language="py",
                    status=ExecutionStatus.COMPLETED,
                    outputs=[],
                    started_at=datetime.now(),
                ),
                SimpleNamespace(),
                None,
                None,
                None,
            )

        orchestrator.execution_service.execute_code = _capture

        ctx = ExecutionContext(
            request=ExecRequest(code="x", lang="py", timeout=5000),
            request_id="r",
            session_id="s",
        )
        await orchestrator._execute_code(ctx)
        # 5000 ms == 5 s exactly.
        assert captured["request"].timeout == 5

    @pytest.mark.asyncio
    async def test_timeout_ms_ceil_for_non_integer_seconds(self, orchestrator):
        from types import SimpleNamespace
        from src.models.execution import CodeExecution, ExecutionStatus
        from src.models.exec import ExecRequest

        captured = {}

        async def _capture(session_id, exec_request, mounted_files, **kwargs):
            captured["request"] = exec_request
            return (
                CodeExecution(
                    execution_id="x",
                    session_id="s",
                    code="",
                    language="py",
                    status=ExecutionStatus.COMPLETED,
                    outputs=[],
                    started_at=datetime.now(),
                ),
                SimpleNamespace(),
                None,
                None,
                None,
            )

        orchestrator.execution_service.execute_code = _capture
        # 4500 ms → ceil(4.5) == 5
        ctx = ExecutionContext(
            request=ExecRequest(code="x", lang="py", timeout=4500),
            request_id="r",
            session_id="s",
        )
        await orchestrator._execute_code(ctx)
        assert captured["request"].timeout == 5

    @pytest.mark.asyncio
    async def test_timeout_none_uses_server_default(self, orchestrator):
        from types import SimpleNamespace
        from src.config import settings
        from src.models.execution import CodeExecution, ExecutionStatus
        from src.models.exec import ExecRequest

        captured = {}

        async def _capture(session_id, exec_request, mounted_files, **kwargs):
            captured["request"] = exec_request
            return (
                CodeExecution(
                    execution_id="x",
                    session_id="s",
                    code="",
                    language="py",
                    status=ExecutionStatus.COMPLETED,
                    outputs=[],
                    started_at=datetime.now(),
                ),
                SimpleNamespace(),
                None,
                None,
                None,
            )

        orchestrator.execution_service.execute_code = _capture

        ctx = ExecutionContext(
            request=ExecRequest(code="x", lang="py"),
            request_id="r",
            session_id="s",
        )
        await orchestrator._execute_code(ctx)
        assert captured["request"].timeout == settings.max_execution_time

    @pytest.mark.asyncio
    async def test_timeout_clamped_to_server_max(self, orchestrator, monkeypatch):
        """The pydantic validator caps `timeout` at 300000 ms == 300 s. The
        orchestrator must additionally clamp to `settings.max_execution_time`
        so a client can't exceed the per-server cap."""
        from types import SimpleNamespace
        from src.config import settings
        from src.models.execution import CodeExecution, ExecutionStatus
        from src.models.exec import ExecRequest

        # Force the server max well below the validator's upper bound so
        # we can observe clamping.
        monkeypatch.setattr(settings, "max_execution_time", 10)

        captured = {}

        async def _capture(session_id, exec_request, mounted_files, **kwargs):
            captured["request"] = exec_request
            return (
                CodeExecution(
                    execution_id="x",
                    session_id="s",
                    code="",
                    language="py",
                    status=ExecutionStatus.COMPLETED,
                    outputs=[],
                    started_at=datetime.now(),
                ),
                SimpleNamespace(),
                None,
                None,
                None,
            )

        orchestrator.execution_service.execute_code = _capture

        ctx = ExecutionContext(
            request=ExecRequest(code="x", lang="py", timeout=300000),
            request_id="r",
            session_id="s",
        )
        await orchestrator._execute_code(ctx)
        assert captured["request"].timeout == 10


class TestHandleGeneratedFilesNestedPaths:
    """Tests that _handle_generated_files preserves subdirectory paths
    (LibreChat PR #12848 expects e.g. name='charts/foo.png')."""

    async def test_nested_path_preserved_in_fileref_name(
        self, orchestrator, mock_file_service
    ):
        from src.models.exec import ExecRequest

        # Mock the helper that pulls bytes out of the container.
        orchestrator._get_file_from_container = AsyncMock(return_value=b"data")
        mock_file_service.store_execution_output_file = AsyncMock(return_value="fid-1")

        request = ExecRequest(code="print()", lang="py")

        # Build a minimal execution stub with a single file output. Use a SimpleNamespace
        # so we don't depend on the full CodeExecution constructor surface.
        from types import SimpleNamespace
        from src.models import OutputType

        execution = SimpleNamespace(
            outputs=[
                SimpleNamespace(
                    type=OutputType.FILE,
                    content="/mnt/data/charts/foo.png",
                    metadata=None,
                )
            ]
        )
        ctx = ExecutionContext(
            request=request,
            request_id="r1",
            session_id="sess-abc",
            execution=execution,
            container=SimpleNamespace(),
        )

        refs = await orchestrator._handle_generated_files(ctx)

        assert len(refs) == 1
        assert refs[0].name == "charts/foo.png"
        # Storage call uses the same nested path as the FileRef name.
        mock_file_service.store_execution_output_file.assert_awaited_once()
        args = mock_file_service.store_execution_output_file.call_args
        assert args.args[1] == "charts/foo.png"

    async def test_top_level_path_unchanged(self, orchestrator, mock_file_service):
        from src.models.exec import ExecRequest
        from types import SimpleNamespace
        from src.models import OutputType

        orchestrator._get_file_from_container = AsyncMock(return_value=b"data")
        mock_file_service.store_execution_output_file = AsyncMock(return_value="fid")

        execution = SimpleNamespace(
            outputs=[
                SimpleNamespace(
                    type=OutputType.FILE,
                    content="/mnt/data/foo.png",
                    metadata=None,
                )
            ]
        )
        ctx = ExecutionContext(
            request=ExecRequest(code="print()", lang="py"),
            request_id="r1",
            session_id="s",
            execution=execution,
            container=SimpleNamespace(),
        )

        refs = await orchestrator._handle_generated_files(ctx)

        assert len(refs) == 1
        assert refs[0].name == "foo.png"

    async def test_hidden_basename_skipped(self, orchestrator, mock_file_service):
        from src.models.exec import ExecRequest
        from types import SimpleNamespace
        from src.models import OutputType

        orchestrator._get_file_from_container = AsyncMock(return_value=b"data")
        mock_file_service.store_execution_output_file = AsyncMock(return_value="fid")

        # Subdirectory is fine, but file basename starts with `.` -> skip.
        execution = SimpleNamespace(
            outputs=[
                SimpleNamespace(
                    type=OutputType.FILE,
                    content="/mnt/data/charts/.hidden.png",
                    metadata=None,
                )
            ]
        )
        ctx = ExecutionContext(
            request=ExecRequest(code="print()", lang="py"),
            request_id="r1",
            session_id="s",
            execution=execution,
            container=SimpleNamespace(),
        )

        refs = await orchestrator._handle_generated_files(ctx)
        assert refs == []
