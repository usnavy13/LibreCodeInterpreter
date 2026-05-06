"""Unit tests for the Programmatic Tool Calling (PTC) models and service.

Tests cover:
- Model validation for PTC request/response/tool models
- ProgrammaticService logic with mocked sandbox
"""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from src.models.files import FileInfo
from src.models.programmatic import (
    PTCFileInput,
    ProgrammaticExecRequest,
    ProgrammaticExecResponse,
    PTCToolCall,
    PTCToolDefinition,
    PTCToolResult,
)
from src.services.programmatic import (
    PTC_DELIMITER,
    PTC_MAX_ROUND_TRIPS,
    PausedContext,
    ProgrammaticService,
)
from src.services.sandbox.nsjail import SandboxInfo

# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def mock_sandbox_info():
    """Create a mock SandboxInfo."""
    return SandboxInfo(
        sandbox_id="test-sandbox-123",
        sandbox_dir=Path("/tmp/test-sandbox"),
        data_dir=Path("/tmp/test-sandbox/data"),
        language="py",
        session_id="test-session",
        created_at=datetime.utcnow(),
        repl_mode=False,
    )


@pytest.fixture
def mock_sandbox_manager(mock_sandbox_info):
    """Create a mock SandboxManager for PTC tests."""
    manager = MagicMock()
    manager.create_sandbox.return_value = mock_sandbox_info
    manager.destroy_sandbox.return_value = True
    manager.copy_content_to_sandbox.return_value = True
    manager.executor = MagicMock()
    manager.executor._build_sanitized_env.return_value = {"PATH": "/usr/bin"}
    return manager


@pytest.fixture
def ptc_service(mock_sandbox_manager):
    """Create a ProgrammaticService with mocked sandbox manager."""
    file_service = AsyncMock()
    return ProgrammaticService(
        sandbox_manager=mock_sandbox_manager,
        file_service=file_service,
    )


# =============================================================================
# MODEL VALIDATION: PTCToolDefinition
# =============================================================================


class TestPTCToolDefinition:
    """Tests for PTCToolDefinition model."""

    def test_minimal_tool_definition(self):
        """Tool with just a name should be valid."""
        tool = PTCToolDefinition(name="get_weather")
        assert tool.name == "get_weather"
        assert tool.description == ""
        assert tool.parameters == {}

    def test_full_tool_definition(self):
        """Tool with all fields should be valid."""
        tool = PTCToolDefinition(
            name="search",
            description="Search the web",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        )
        assert tool.name == "search"
        assert tool.description == "Search the web"
        assert "properties" in tool.parameters

    def test_tool_definition_requires_name(self):
        """Tool without name should fail validation."""
        with pytest.raises(ValidationError):
            PTCToolDefinition()


# =============================================================================
# MODEL VALIDATION: PTCToolCall
# =============================================================================


class TestPTCToolCall:
    """Tests for PTCToolCall model."""

    def test_valid_tool_call(self):
        """Tool call with id and name should be valid."""
        call = PTCToolCall(id="call-1", name="get_weather")
        assert call.id == "call-1"
        assert call.name == "get_weather"
        assert call.input == {}

    def test_tool_call_with_input(self):
        """Tool call with input arguments should be valid."""
        call = PTCToolCall(
            id="call-2",
            name="search",
            input={"query": "python", "limit": 10},
        )
        assert call.input == {"query": "python", "limit": 10}

    def test_tool_call_requires_id(self):
        """Tool call without id should fail validation."""
        with pytest.raises(ValidationError):
            PTCToolCall(name="get_weather")

    def test_tool_call_requires_name(self):
        """Tool call without name should fail validation."""
        with pytest.raises(ValidationError):
            PTCToolCall(id="call-1")


# =============================================================================
# MODEL VALIDATION: PTCToolResult
# =============================================================================


class TestPTCToolResult:
    """Tests for PTCToolResult model."""

    def test_valid_result(self):
        """Tool result with call_id and result should be valid."""
        result = PTCToolResult(call_id="call-1", result={"temp": 72})
        assert result.call_id == "call-1"
        assert result.result == {"temp": 72}
        assert result.is_error is False
        assert result.error_message is None

    def test_error_result(self):
        """Tool result with error should be valid."""
        result = PTCToolResult(
            call_id="call-1",
            is_error=True,
            error_message="Tool not found",
        )
        assert result.is_error is True
        assert result.error_message == "Tool not found"
        assert result.result is None

    def test_result_requires_call_id(self):
        """Tool result without call_id should fail validation."""
        with pytest.raises(ValidationError):
            PTCToolResult(result="data")

    def test_result_with_string_value(self):
        """Tool result with string value should be valid."""
        result = PTCToolResult(call_id="call-1", result="plain text")
        assert result.result == "plain text"

    def test_result_with_none_value(self):
        """Tool result with None should be valid (default)."""
        result = PTCToolResult(call_id="call-1")
        assert result.result is None


# =============================================================================
# MODEL VALIDATION: ProgrammaticExecRequest
# =============================================================================


class TestProgrammaticExecRequest:
    """Tests for ProgrammaticExecRequest model."""

    def test_initial_request_with_code(self):
        """Initial request with code should be valid."""
        req = ProgrammaticExecRequest(
            code="print('hello')",
            tools=[PTCToolDefinition(name="tool1")],
        )
        assert req.code == "print('hello')"
        assert len(req.tools) == 1
        assert req.continuation_token is None
        assert req.tool_results == []

    def test_initial_request_with_all_fields(self):
        """Initial request with all optional fields should be valid."""
        req = ProgrammaticExecRequest(
            code="print('hello')",
            tools=[PTCToolDefinition(name="tool1")],
            session_id="sess-123",
            user_id="user-456",
            entity_id="asst_abc",
            timeout=60000,
            files=[
                {
                    "session_id": "source-session",
                    "id": "file-123",
                    "name": "test.txt",
                }
            ],
        )
        assert req.session_id == "sess-123"
        assert req.user_id == "user-456"
        assert req.entity_id == "asst_abc"
        assert req.timeout == 60000
        assert len(req.files) == 1
        assert isinstance(req.files[0], PTCFileInput)

    def test_request_accepts_file_reference_shape(self):
        """LibreChat-style file references should validate."""
        req = ProgrammaticExecRequest(
            code="print('hello')",
            files=[
                {
                    "session_id": "sess-123",
                    "id": "file-123",
                    "name": "report.csv",
                }
            ],
        )
        assert req.files[0].session_id == "sess-123"
        assert req.files[0].id == "file-123"
        assert req.files[0].name == "report.csv"

    def test_timeout_validation_is_milliseconds(self):
        """PTC timeout should use the public millisecond contract."""
        with pytest.raises(ValidationError):
            ProgrammaticExecRequest(code="x", timeout=60)

    def test_request_rejects_legacy_inline_file_shape(self):
        """PTC no longer accepts inline {filename, content} payloads."""
        with pytest.raises(ValidationError):
            ProgrammaticExecRequest(
                code="print('hello')",
                files=[{"filename": "test.txt", "content": "data"}],
            )

    def test_continuation_request(self):
        """Continuation request with token and results should be valid."""
        req = ProgrammaticExecRequest(
            continuation_token="abc123",
            tool_results=[
                PTCToolResult(call_id="call-1", result="data"),
            ],
        )
        assert req.continuation_token == "abc123"
        assert len(req.tool_results) == 1
        assert req.code is None

    def test_empty_request_is_valid(self):
        """Empty request should pass model validation (API handles logic)."""
        req = ProgrammaticExecRequest()
        assert req.code is None
        assert req.continuation_token is None

    def test_entity_id_pattern_valid(self):
        """Entity ID with valid pattern should pass."""
        req = ProgrammaticExecRequest(code="x", entity_id="asst_abc-123")
        assert req.entity_id == "asst_abc-123"

    def test_entity_id_pattern_invalid(self):
        """Entity ID with invalid characters should fail validation."""
        with pytest.raises(ValidationError):
            ProgrammaticExecRequest(code="x", entity_id="invalid entity!@#")

    def test_entity_id_max_length(self):
        """Entity ID exceeding max length should fail validation."""
        with pytest.raises(ValidationError):
            ProgrammaticExecRequest(code="x", entity_id="a" * 41)

    def test_request_no_tools_defaults_empty(self):
        """Request without tools should default to empty list."""
        req = ProgrammaticExecRequest(code="print('hello')")
        assert req.tools == []

    def test_request_no_files_defaults_empty(self):
        """Request without files should default to empty list."""
        req = ProgrammaticExecRequest(code="print('hello')")
        assert req.files == []


# =============================================================================
# MODEL VALIDATION: ProgrammaticExecResponse
# =============================================================================


class TestProgrammaticExecResponse:
    """Tests for ProgrammaticExecResponse model."""

    def test_completed_response(self):
        """Completed response should have status=completed."""
        resp = ProgrammaticExecResponse(
            status="completed",
            session_id="sess-123",
            stdout="Hello, World!\n",
        )
        assert resp.status == "completed"
        assert resp.session_id == "sess-123"
        assert resp.stdout == "Hello, World!\n"
        assert resp.continuation_token is None
        assert resp.tool_calls == []
        assert resp.error is None

    def test_tool_call_required_response(self):
        """Tool call required response should have token and calls."""
        resp = ProgrammaticExecResponse(
            status="tool_call_required",
            session_id="sess-123",
            continuation_token="token-abc",
            tool_calls=[
                PTCToolCall(id="call-1", name="search", input={"q": "test"}),
            ],
        )
        assert resp.status == "tool_call_required"
        assert resp.continuation_token == "token-abc"
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "search"

    def test_error_response(self):
        """Error response should have status=error and error message."""
        resp = ProgrammaticExecResponse(
            status="error",
            error="Something went wrong",
        )
        assert resp.status == "error"
        assert resp.error == "Something went wrong"
        assert resp.session_id is None

    def test_response_defaults(self):
        """Response should have sensible defaults."""
        resp = ProgrammaticExecResponse(status="completed")
        assert resp.stdout == ""
        assert resp.stderr == ""
        assert resp.files == []
        assert resp.tool_calls == []
        assert resp.continuation_token is None
        assert resp.error is None

    def test_response_requires_status(self):
        """Response without status should fail validation."""
        with pytest.raises(ValidationError):
            ProgrammaticExecResponse()


# =============================================================================
# SERVICE: start_execution
# =============================================================================


class TestProgrammaticServiceStartExecution:
    """Tests for ProgrammaticService.start_execution."""

    async def test_start_execution_ptc_server_not_found(
        self, ptc_service, mock_sandbox_manager
    ):
        """Should return error if ptc_server.py not found."""
        with patch("pathlib.Path.exists", return_value=False):
            response = await ptc_service.start_execution(
                code="print('hello')",
                tools=[],
                session_id="sess-123",
            )

        assert response.status == "error"
        assert "PTC server script not found" in response.error

    async def test_start_execution_creates_sandbox(
        self, ptc_service, mock_sandbox_manager
    ):
        """Should create sandbox with correct parameters."""
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.read_bytes", return_value=b"# ptc_server.py"),
            patch.object(
                ptc_service._nsjail_config,
                "build_args",
                return_value=["--config", "/tmp/test.cfg"],
            ),
            patch(
                "asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
            ) as mock_subprocess,
        ):
            # Mock process that returns completed response
            mock_proc = AsyncMock()
            mock_proc.stdin = AsyncMock()
            mock_proc.stdin.write = MagicMock()
            mock_proc.stdin.drain = AsyncMock()
            mock_proc.returncode = None
            mock_proc.pid = 12345

            completed_response = (
                json.dumps({"type": "completed", "stdout": "hello\n", "stderr": ""})
                + PTC_DELIMITER
            )

            mock_proc.stdout = AsyncMock()
            mock_proc.stdout.read = AsyncMock(return_value=completed_response.encode())
            mock_proc.stderr = AsyncMock()
            mock_proc.stderr.read = AsyncMock(return_value=b"")

            mock_subprocess.return_value = mock_proc

            await ptc_service.start_execution(
                code="print('hello')",
                tools=[],
                session_id="sess-123",
            )

        mock_sandbox_manager.create_sandbox.assert_called_once_with(
            session_id="sess-123",
            language="py",
            repl_mode=False,
        )

    async def test_start_execution_cleanup_on_exception(
        self, ptc_service, mock_sandbox_manager
    ):
        """Should destroy sandbox on exception."""
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.read_bytes", return_value=b"# ptc_server.py"),
            patch.object(
                ptc_service._nsjail_config,
                "build_args",
                return_value=[],
            ),
            patch(
                "asyncio.create_subprocess_exec",
                side_effect=OSError("Cannot start process"),
            ),
        ):
            response = await ptc_service.start_execution(
                code="print('hello')",
                tools=[],
                session_id="sess-123",
            )

        assert response.status == "error"
        assert "Execution failed" in response.error
        mock_sandbox_manager.destroy_sandbox.assert_called_once()

    async def test_start_execution_mounts_referenced_files(
        self, ptc_service, mock_sandbox_manager
    ):
        """LibreChat-style file refs should be resolved and mounted."""
        ptc_service._file_service.get_file_info.return_value = FileInfo(
            file_id="file-123",
            filename="server-side.bin",
            size=14,
            content_type="text/csv",
            created_at=datetime.utcnow(),
            path="/report.csv",
        )
        ptc_service._file_service.get_file_content.return_value = b"col1,col2\n1,2\n"

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.read_bytes", return_value=b"# ptc_server.py"),
            patch.object(
                ptc_service._nsjail_config,
                "build_args",
                return_value=["--config", "/tmp/x"],
            ),
            patch(
                "asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
            ) as mock_subprocess,
        ):
            mock_proc = AsyncMock()
            mock_proc.stdin = AsyncMock()
            mock_proc.stdin.write = MagicMock()
            mock_proc.stdin.drain = AsyncMock()
            mock_proc.returncode = None
            mock_proc.pid = 12345
            mock_proc.stdout = AsyncMock()
            mock_proc.stdout.read = AsyncMock(
                return_value=(
                    json.dumps({"type": "completed", "stdout": "ok\n", "stderr": ""})
                    + PTC_DELIMITER
                ).encode()
            )
            mock_proc.stderr = AsyncMock()
            mock_proc.stderr.read = AsyncMock(return_value=b"")
            mock_subprocess.return_value = mock_proc

            response = await ptc_service.start_execution(
                code="print('hello')",
                tools=[],
                session_id="sess-123",
                files=[
                    PTCFileInput(
                        session_id="upload-session",
                        id="file-123",
                        name="nested/report.csv",
                    )
                ],
            )

        assert response.status == "completed"
        ptc_service._file_service.get_file_info.assert_awaited_once_with(
            "upload-session",
            "file-123",
        )
        ptc_service._file_service.get_file_content.assert_awaited_once_with(
            "upload-session",
            "file-123",
        )
        # Subdirectories are preserved (Item 4b symmetry — LibreChat skill
        # bundles ship `skills/<name>/SKILL.md` and expect to read them at
        # the nested path inside the sandbox).
        assert mock_sandbox_manager.copy_content_to_sandbox.call_args_list[1].args == (
            mock_sandbox_manager.create_sandbox.return_value,
            b"col1,col2\n1,2\n",
            "/mnt/data/nested/report.csv",
        )

    async def test_start_execution_errors_when_referenced_file_missing(
        self, ptc_service, mock_sandbox_manager
    ):
        """Missing file refs should fail before the PTC process starts."""
        ptc_service._file_service.get_file_info.return_value = None
        ptc_service._file_service.get_file_content.return_value = None

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.read_bytes", return_value=b"# ptc_server.py"),
            patch(
                "asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_proc,
        ):
            response = await ptc_service.start_execution(
                code="print('hello')",
                tools=[],
                session_id="sess-123",
                files=[
                    PTCFileInput(
                        session_id="upload-session",
                        id="file-404",
                        name="missing.csv",
                    )
                ],
            )

        assert response.status == "error"
        assert "Referenced PTC file metadata could not be loaded" in response.error
        mock_proc.assert_not_called()

    async def test_continue_uses_remaining_execution_timeout(self, ptc_service):
        """Continuation should keep using the initial execution timeout budget."""
        token = "timeout-token"
        mock_proc = AsyncMock()
        mock_proc.returncode = None
        mock_proc.pid = 12345
        mock_proc.stdin = AsyncMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()

        ctx = PausedContext(
            sandbox_info=SandboxInfo(
                sandbox_id="sb-timeout",
                sandbox_dir=Path("/tmp/sb-timeout"),
                data_dir=Path("/tmp/sb-timeout/data"),
                language="py",
                session_id="sess-timeout",
                created_at=datetime.utcnow(),
                repl_mode=False,
            ),
            process=mock_proc,
            session_id="sess-timeout",
            execution_deadline=100.0,
            execution_timeout_seconds=60,
        )
        ptc_service._paused_contexts[token] = ctx

        with (
            patch("time.monotonic", return_value=70.2),
            patch.object(
                ptc_service,
                "_read_ptc_response",
                new_callable=AsyncMock,
            ) as mock_read,
        ):
            mock_read.return_value = ProgrammaticExecResponse(status="completed")
            await ptc_service.continue_execution(
                continuation_token=token,
                tool_results=[PTCToolResult(call_id="c1", result="ok")],
            )

        assert mock_read.await_args.kwargs["timeout"] == 30
        assert mock_read.await_args.kwargs["execution_timeout_seconds"] == 60


# =============================================================================
# SERVICE: continue_execution
# =============================================================================


class TestProgrammaticServiceContinueExecution:
    """Tests for ProgrammaticService.continue_execution."""

    async def test_continue_invalid_token(self, ptc_service):
        """Should return error for invalid continuation token."""
        response = await ptc_service.continue_execution(
            continuation_token="nonexistent-token",
            tool_results=[],
        )

        assert response.status == "error"
        assert "Invalid or expired continuation token" in response.error

    async def test_continue_max_round_trips_exceeded(self, ptc_service):
        """Should return error when max round trips exceeded."""
        token = "test-token-123"

        # Create a paused context at max round trips
        mock_proc = AsyncMock()
        mock_proc.returncode = None
        mock_proc.pid = 12345

        ctx = PausedContext(
            sandbox_info=SandboxInfo(
                sandbox_id="sb-1",
                sandbox_dir=Path("/tmp/sb"),
                data_dir=Path("/tmp/sb/data"),
                language="py",
                session_id="sess-1",
                created_at=datetime.utcnow(),
                repl_mode=False,
            ),
            process=mock_proc,
            session_id="sess-1",
            round_trip_count=PTC_MAX_ROUND_TRIPS,
        )
        ptc_service._paused_contexts[token] = ctx

        response = await ptc_service.continue_execution(
            continuation_token=token,
            tool_results=[PTCToolResult(call_id="c1", result="ok")],
        )

        assert response.status == "error"
        assert "Maximum round trips" in response.error
        # Context should be cleaned up
        assert token not in ptc_service._paused_contexts

    async def test_continue_cancels_timeout(self, ptc_service):
        """Should cancel timeout handle when continuing."""
        token = "test-token-456"

        mock_proc = AsyncMock()
        mock_proc.returncode = None
        mock_proc.pid = 12345
        mock_proc.stdin = AsyncMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()

        mock_timeout = MagicMock()

        completed_response = (
            json.dumps({"type": "completed", "stdout": "done\n", "stderr": ""})
            + PTC_DELIMITER
        )
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.read = AsyncMock(return_value=completed_response.encode())
        mock_proc.stderr = AsyncMock()
        mock_proc.stderr.read = AsyncMock(return_value=b"")

        ctx = PausedContext(
            sandbox_info=SandboxInfo(
                sandbox_id="sb-2",
                sandbox_dir=Path("/tmp/sb2"),
                data_dir=Path("/tmp/sb2/data"),
                language="py",
                session_id="sess-2",
                created_at=datetime.utcnow(),
                repl_mode=False,
            ),
            process=mock_proc,
            session_id="sess-2",
            round_trip_count=0,
            timeout_handle=mock_timeout,
        )
        ptc_service._paused_contexts[token] = ctx

        with patch.object(ptc_service, "_sandbox_manager"):
            await ptc_service.continue_execution(
                continuation_token=token,
                tool_results=[PTCToolResult(call_id="c1", result="ok")],
            )

        mock_timeout.cancel.assert_called_once()


class TestProgrammaticServiceReadResponse:
    """Tests for low-level PTC response handling."""

    async def test_read_response_reports_timeout_when_process_exits_at_deadline(
        self, ptc_service, mock_sandbox_info
    ):
        """EOF at the execution deadline should surface as a timeout."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 137
        mock_proc.pid = 12345
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.read = AsyncMock(return_value=b"")
        mock_proc.stderr = AsyncMock()
        mock_proc.stderr.read = AsyncMock(return_value=b"")

        with patch("time.monotonic", return_value=10.0):
            response = await ptc_service._read_ptc_response(
                proc=mock_proc,
                sandbox_info=mock_sandbox_info,
                session_id="sess-timeout",
                timeout=1,
                execution_deadline=10.0,
                execution_timeout_seconds=1,
            )

        assert response.status == "error"
        assert response.error == "Execution timed out after 1 seconds"


# =============================================================================
# SERVICE: cleanup
# =============================================================================


class TestProgrammaticServiceCleanup:
    """Tests for ProgrammaticService cleanup methods."""

    async def test_cleanup_paused_context(self, ptc_service):
        """Should clean up a specific paused context."""
        token = "cleanup-token"

        mock_proc = AsyncMock()
        mock_proc.returncode = None
        mock_proc.pid = 12345
        mock_proc.wait = AsyncMock()

        mock_timeout = MagicMock()

        ctx = PausedContext(
            sandbox_info=SandboxInfo(
                sandbox_id="sb-3",
                sandbox_dir=Path("/tmp/sb3"),
                data_dir=Path("/tmp/sb3/data"),
                language="py",
                session_id="sess-3",
                created_at=datetime.utcnow(),
                repl_mode=False,
            ),
            process=mock_proc,
            session_id="sess-3",
            timeout_handle=mock_timeout,
        )
        ptc_service._paused_contexts[token] = ctx

        await ptc_service._cleanup_paused_context(token)

        assert token not in ptc_service._paused_contexts
        mock_timeout.cancel.assert_called_once()
        ptc_service._sandbox_manager.destroy_sandbox.assert_called_once()

    async def test_cleanup_nonexistent_token(self, ptc_service):
        """Should handle cleanup of nonexistent token gracefully."""
        await ptc_service._cleanup_paused_context("does-not-exist")
        # Should not raise

    async def test_cleanup_all(self, ptc_service):
        """Should clean up all paused contexts."""
        for i in range(3):
            token = f"token-{i}"
            mock_proc = AsyncMock()
            mock_proc.returncode = None
            mock_proc.pid = 12345 + i
            mock_proc.wait = AsyncMock()

            ctx = PausedContext(
                sandbox_info=SandboxInfo(
                    sandbox_id=f"sb-{i}",
                    sandbox_dir=Path(f"/tmp/sb-{i}"),
                    data_dir=Path(f"/tmp/sb-{i}/data"),
                    language="py",
                    session_id=f"sess-{i}",
                    created_at=datetime.utcnow(),
                    repl_mode=False,
                ),
                process=mock_proc,
                session_id=f"sess-{i}",
            )
            ptc_service._paused_contexts[token] = ctx

        await ptc_service.cleanup_all()

        assert len(ptc_service._paused_contexts) == 0


class TestStartExecutionLangRouting:
    """start_execution(lang=...) must select the matching PTC server script
    and create the sandbox in the matching language.

    We short-circuit at copy_content_to_sandbox so we don't have to set up
    nsjail / unshare / a real subprocess just to verify the routing.
    """

    async def _exercise_start(self, lang: str):
        ptc_service = ProgrammaticService()

        sandbox_info = SandboxInfo(
            sandbox_id="sb-test",
            sandbox_dir=Path("/tmp/sb-test"),
            data_dir=Path("/tmp/sb-test/data"),
            language=lang,
            session_id="sess-1",
            created_at=datetime.utcnow(),
            repl_mode=False,
        )
        ptc_service._sandbox_manager = MagicMock()
        ptc_service._sandbox_manager.create_sandbox.return_value = sandbox_info
        # Make copy_content_to_sandbox raise so we abort before nsjail/subprocess.
        boom = RuntimeError("__short_circuit__")
        ptc_service._sandbox_manager.copy_content_to_sandbox.side_effect = boom

        with patch("src.services.programmatic.Path") as mock_path_cls:
            inst = mock_path_cls.return_value
            inst.exists.return_value = True
            inst.read_bytes.return_value = b"# fake script"
            # Path("/opt") / filename should also resolve through the mock.
            inst.__truediv__ = lambda self, other: inst

            response = await ptc_service.start_execution(
                code="print('hi')" if lang == "py" else "echo hi",
                tools=[],
                session_id="sess-1",
                lang=lang,
            )

        create_kwargs = ptc_service._sandbox_manager.create_sandbox.call_args.kwargs
        copy_args = ptc_service._sandbox_manager.copy_content_to_sandbox.call_args.args
        return response, create_kwargs, copy_args

    async def test_lang_py_routes_to_python_server(self):
        response, create_kwargs, copy_args = await self._exercise_start("py")
        assert create_kwargs["language"] == "py"
        # 3rd positional arg is the destination path under /mnt/data.
        assert copy_args[2] == "/mnt/data/ptc_server.py"
        # We intentionally raised inside copy, so this is an error response —
        # the routing assertions above are the real check.
        assert response.status == "error"

    async def test_lang_bash_routes_to_bash_server(self):
        response, create_kwargs, copy_args = await self._exercise_start("bash")
        assert create_kwargs["language"] == "bash"
        assert copy_args[2] == "/mnt/data/ptc_bash_server.py"
        assert response.status == "error"

    async def test_invalid_lang_short_circuits_before_sandbox(self):
        ptc_service = ProgrammaticService()
        ptc_service._sandbox_manager = MagicMock()

        response = await ptc_service.start_execution(
            code="x", tools=[], session_id="s", lang="ruby"
        )

        assert response.status == "error"
        assert "Unsupported PTC lang" in (response.error or "")
        # No sandbox creation attempt for an invalid lang.
        ptc_service._sandbox_manager.create_sandbox.assert_not_called()
