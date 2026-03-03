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
        assert ctx.mounted_file_refs == []

    @pytest.mark.asyncio
    async def test_mount_files_explicit_files_takes_precedence(
        self, orchestrator, mock_file_service
    ):
        """When explicit files provided, should use those instead of auto-mount."""
        from src.models.exec import RequestFile

        # Setup: explicit file
        mock_file_service.get_file_info = AsyncMock(
            return_value=FileInfo(
                file_id="explicit-file",
                filename="explicit.txt",
                size=50,
                content_type="text/plain",
                created_at=datetime.now(),
                path="/mnt/data/explicit.txt",
            )
        )
        mock_file_service.list_files = AsyncMock(return_value=[])

        request = ExecRequest(
            code="print('hello')",
            lang="py",
            files=[
                RequestFile(
                    id="explicit-file", session_id="other-session", name="explicit.txt"
                ),
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

        request = ExecRequest(code="print('hello')", lang="py")
        ctx = ExecutionContext(
            request=request,
            request_id="test-123",
            session_id="test-session-123",
        )

        result = await orchestrator._auto_mount_session_files(ctx)

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_auto_mount_tracks_file_refs(self, orchestrator, mock_file_service):
        """Auto-mount should track file refs for state linking."""
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
                RequestFile(
                    id="file-1", session_id="user-session-S2", name="data.csv"
                ),
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
                RequestFile(
                    id="file-1", session_id="user-session-S2", name="data.csv"
                ),
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
    async def test_explicit_mount_files(
        self, orchestrator, mock_file_service
    ):
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
