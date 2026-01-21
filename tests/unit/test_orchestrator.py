"""Unit tests for the execution orchestrator."""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from src.services.orchestrator import ExecutionOrchestrator, ExecutionContext
from src.models.exec import ExecRequest, FileRef
from src.models.files import FileInfo
from src.models.session import Session, SessionStatus


@pytest.fixture
def mock_session_service():
    """Create a mock session service."""
    service = AsyncMock()
    service.get_session = AsyncMock(return_value=Session(
        session_id="test-session-123",
        status=SessionStatus.ACTIVE,
        created_at=datetime.now(),
        last_activity=datetime.now(),
        expires_at=datetime.now(),
        files={},
        metadata={},
        working_directory="/workspace",
    ))
    service.create_session = AsyncMock(return_value=Session(
        session_id="new-session-456",
        status=SessionStatus.ACTIVE,
        created_at=datetime.now(),
        last_activity=datetime.now(),
        expires_at=datetime.now(),
        files={},
        metadata={},
        working_directory="/workspace",
    ))
    service.list_sessions_by_entity = AsyncMock(return_value=[])
    return service


@pytest.fixture
def mock_file_service():
    """Create a mock file service."""
    service = AsyncMock()
    service.get_file_info = AsyncMock(return_value=None)
    service.list_files = AsyncMock(return_value=[])
    service._get_file_metadata = AsyncMock(return_value=None)
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
        assert ctx.mounted_file_refs is None

    @pytest.mark.asyncio
    async def test_mount_files_with_session_id_auto_mounts(
        self, orchestrator, mock_file_service
    ):
        """When session_id exists but no explicit files, should auto-mount all session files."""
        # Setup: session has two files (one uploaded, one generated)
        mock_file_service.list_files = AsyncMock(return_value=[
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
        ])

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

        # Verify file refs were tracked for state linking
        assert ctx.mounted_file_refs is not None
        assert len(ctx.mounted_file_refs) == 2

    @pytest.mark.asyncio
    async def test_mount_files_empty_session(
        self, orchestrator, mock_file_service
    ):
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
        assert ctx.mounted_file_refs == []

    @pytest.mark.asyncio
    async def test_mount_files_explicit_files_takes_precedence(
        self, orchestrator, mock_file_service
    ):
        """When explicit files provided, should use those instead of auto-mount."""
        from src.models.exec import RequestFile

        # Setup: explicit file
        mock_file_service.get_file_info = AsyncMock(return_value=FileInfo(
            file_id="explicit-file",
            filename="explicit.txt",
            size=50,
            content_type="text/plain",
            created_at=datetime.now(),
            path="/mnt/data/explicit.txt",
        ))
        mock_file_service.list_files = AsyncMock(return_value=[])

        request = ExecRequest(
            code="print('hello')",
            lang="py",
            files=[
                RequestFile(id="explicit-file", session_id="other-session", name="explicit.txt"),
            ],
        )
        ctx = ExecutionContext(
            request=request,
            request_id="test-123",
            session_id="test-session-123",
        )

        result = await orchestrator._mount_files(ctx)

        # Verify only explicit file was mounted
        assert len(result) == 1
        assert result[0]["file_id"] == "explicit-file"
        assert result[0]["filename"] == "explicit.txt"
        assert result[0]["session_id"] == "other-session"  # Uses file's session_id

        # Verify get_file_info was called, not list_files for auto-mount
        mock_file_service.get_file_info.assert_called_once()


class TestAutoMountSessionFiles:
    """Tests specifically for the auto-mount behavior."""

    @pytest.mark.asyncio
    async def test_auto_mount_deduplicates_files(
        self, orchestrator, mock_file_service
    ):
        """Auto-mount should skip duplicate files."""
        mock_file_service.list_files = AsyncMock(return_value=[
            FileInfo(
                file_id="file-1",
                filename="data.csv",
                size=100,
                content_type="text/csv",
                created_at=datetime.now(),
                path="/mnt/data/data.csv",
            ),
        ])

        request = ExecRequest(code="print('hello')", lang="py")
        ctx = ExecutionContext(
            request=request,
            request_id="test-123",
            session_id="test-session-123",
        )

        result = await orchestrator._auto_mount_session_files(ctx)

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_auto_mount_tracks_file_refs(
        self, orchestrator, mock_file_service
    ):
        """Auto-mount should track file refs for state linking."""
        mock_file_service.list_files = AsyncMock(return_value=[
            FileInfo(
                file_id="file-1",
                filename="data.csv",
                size=100,
                content_type="text/csv",
                created_at=datetime.now(),
                path="/mnt/data/data.csv",
            ),
        ])

        request = ExecRequest(code="print('hello')", lang="py")
        ctx = ExecutionContext(
            request=request,
            request_id="test-123",
            session_id="test-session-123",
        )

        await orchestrator._auto_mount_session_files(ctx)

        assert ctx.mounted_file_refs == [
            {"session_id": "test-session-123", "file_id": "file-1"},
        ]


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


class TestExplicitFileMounting:
    """Tests for explicit file mounting behavior."""

    @pytest.mark.asyncio
    async def test_explicit_mount_with_restore_state(
        self, orchestrator, mock_file_service
    ):
        """Explicit mount should handle restore_state flag."""
        from src.models.exec import RequestFile

        mock_file_service.get_file_info = AsyncMock(return_value=FileInfo(
            file_id="file-1",
            filename="data.csv",
            size=100,
            content_type="text/csv",
            created_at=datetime.now(),
            path="/mnt/data/data.csv",
            state_hash="abc123",
        ))

        request = ExecRequest(
            code="print('hello')",
            lang="py",
            files=[
                RequestFile(
                    id="file-1",
                    session_id="test-session",
                    name="data.csv",
                    restore_state=True,
                ),
            ],
        )
        ctx = ExecutionContext(
            request=request,
            request_id="test-123",
            session_id="test-session",
        )

        # Mock the state loading
        with patch.object(orchestrator, '_load_state_by_hash', new_callable=AsyncMock) as mock_load:
            with patch('src.services.orchestrator.settings') as mock_settings:
                mock_settings.state_persistence_enabled = True

                result = await orchestrator._mount_explicit_files(ctx)

                # Verify state loading was triggered
                mock_load.assert_called_once_with(ctx, "abc123")

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_explicit_mount_fallback_to_name_lookup(
        self, orchestrator, mock_file_service
    ):
        """Explicit mount should fallback to name lookup if ID not found."""
        from src.models.exec import RequestFile

        # First call returns None (ID not found), second returns file list
        mock_file_service.get_file_info = AsyncMock(return_value=None)
        mock_file_service.list_files = AsyncMock(return_value=[
            FileInfo(
                file_id="actual-file-id",
                filename="data.csv",
                size=100,
                content_type="text/csv",
                created_at=datetime.now(),
                path="/mnt/data/data.csv",
            ),
        ])

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
