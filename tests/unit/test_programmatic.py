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

from src.models.programmatic import (
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
    return ProgrammaticService(sandbox_manager=mock_sandbox_manager)


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
            timeout=60,
            files=[{"filename": "test.txt", "content": "data"}],
        )
        assert req.session_id == "sess-123"
        assert req.user_id == "user-456"
        assert req.entity_id == "asst_abc"
        assert req.timeout == 60
        assert len(req.files) == 1

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
            patch("src.services.programmatic.NsjailConfig") as mock_nsjail_config,
            patch(
                "asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
            ) as mock_subprocess,
        ):
            mock_nsjail_config.return_value.build_args.return_value = [
                "--config",
                "/tmp/test.cfg",
            ]

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
            patch("src.services.programmatic.NsjailConfig") as mock_nsjail_config,
            patch(
                "asyncio.create_subprocess_exec",
                side_effect=OSError("Cannot start process"),
            ),
        ):
            mock_nsjail_config.return_value.build_args.return_value = []

            response = await ptc_service.start_execution(
                code="print('hello')",
                tools=[],
                session_id="sess-123",
            )

        assert response.status == "error"
        assert "Execution failed" in response.error
        mock_sandbox_manager.destroy_sandbox.assert_called_once()


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
