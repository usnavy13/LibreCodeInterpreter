"""Contract tests for the Programmatic Tool Calling (PTC) API endpoint.

These tests validate request parsing, response shape, and timeout conversion
with a mocked PTC service. They are not end-to-end PTC execution coverage.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from src.main import app
from src.models.programmatic import (
    ProgrammaticExecResponse,
    PTCToolCall,
)
from src.models.session import Session, SessionStatus
from datetime import datetime, timezone


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Provide authentication headers for tests."""
    return {"x-api-key": "test-api-key-for-testing-12345"}


@pytest.fixture
def mock_session():
    """Create a mock session for session service."""
    return Session(
        session_id="ptc-session-123",
        status=SessionStatus.ACTIVE,
        created_at=datetime.now(timezone.utc),
        last_activity=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc),
        metadata={},
    )


@pytest.fixture
def mock_ptc_completed_response():
    """A completed PTC response."""
    return ProgrammaticExecResponse(
        status="completed",
        session_id="ptc-session-123",
        stdout="Hello from PTC\n",
        stderr="",
    )


@pytest.fixture
def mock_ptc_tool_call_response():
    """A tool_call_required PTC response."""
    return ProgrammaticExecResponse(
        status="tool_call_required",
        session_id="ptc-session-123",
        continuation_token="cont-token-abc",
        tool_calls=[
            PTCToolCall(id="call-1", name="get_weather", input={"city": "NYC"}),
        ],
        stdout="",
        stderr="",
    )


@pytest.fixture
def mock_ptc_error_response():
    """An error PTC response."""
    return ProgrammaticExecResponse(
        status="error",
        error="Invalid or expired continuation token",
    )


# =============================================================================
# INITIAL EXECUTION
# =============================================================================


class TestProgrammaticInitialExecution:
    """Tests for POST /exec/programmatic with initial execution."""

    @patch("src.api.programmatic._get_ptc_service")
    def test_initial_request_returns_completed(
        self,
        mock_get_service,
        client,
        auth_headers,
        mock_session,
        mock_ptc_completed_response,
    ):
        """Initial request with code should return completed response."""
        mock_service = AsyncMock()
        mock_service.start_execution.return_value = mock_ptc_completed_response
        mock_get_service.return_value = mock_service

        with patch(
            "src.api.programmatic.SessionServiceDep",
        ):
            from src.dependencies.services import get_session_service

            mock_session_svc = AsyncMock()
            mock_session_svc.create_session.return_value = mock_session
            app.dependency_overrides[get_session_service] = lambda: mock_session_svc

            try:
                response = client.post(
                    "/exec/programmatic",
                    json={
                        "code": "print('hello')",
                        "tools": [
                            {"name": "get_weather", "description": "Get weather"}
                        ],
                    },
                    headers=auth_headers,
                )
            finally:
                app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["session_id"] == "ptc-session-123"
        assert data["stdout"] == "Hello from PTC\n"
        assert mock_service.start_execution.await_args.kwargs["timeout"] is None

    @patch("src.api.programmatic._get_ptc_service")
    def test_initial_request_returns_tool_calls(
        self,
        mock_get_service,
        client,
        auth_headers,
        mock_session,
        mock_ptc_tool_call_response,
    ):
        """Initial request should return tool_call_required when code calls tools."""
        mock_service = AsyncMock()
        mock_service.start_execution.return_value = mock_ptc_tool_call_response
        mock_get_service.return_value = mock_service

        from src.dependencies.services import get_session_service

        mock_session_svc = AsyncMock()
        mock_session_svc.create_session.return_value = mock_session
        app.dependency_overrides[get_session_service] = lambda: mock_session_svc

        try:
            response = client.post(
                "/exec/programmatic",
                json={
                    "code": "result = await get_weather(city='NYC')",
                    "tools": [
                        {
                            "name": "get_weather",
                            "description": "Get weather",
                            "parameters": {
                                "type": "object",
                                "properties": {"city": {"type": "string"}},
                            },
                        }
                    ],
                },
                headers=auth_headers,
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "tool_call_required"
        assert data["continuation_token"] == "cont-token-abc"
        assert len(data["tool_calls"]) == 1
        assert data["tool_calls"][0]["name"] == "get_weather"
        assert data["tool_calls"][0]["id"] == "call-1"

    @patch("src.api.programmatic._get_ptc_service")
    def test_initial_request_with_session_id(
        self,
        mock_get_service,
        client,
        auth_headers,
        mock_session,
        mock_ptc_completed_response,
    ):
        """Initial request with session_id should use existing session."""
        mock_service = AsyncMock()
        mock_service.start_execution.return_value = mock_ptc_completed_response
        mock_get_service.return_value = mock_service

        from src.dependencies.services import get_session_service

        mock_session_svc = AsyncMock()
        app.dependency_overrides[get_session_service] = lambda: mock_session_svc

        try:
            response = client.post(
                "/exec/programmatic",
                json={
                    "code": "print('hello')",
                    "session_id": "existing-session-456",
                },
                headers=auth_headers,
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        # Should not have created a new session
        mock_session_svc.create_session.assert_not_called()

    @patch("src.api.programmatic._get_ptc_service")
    def test_initial_request_converts_timeout_ms_to_seconds(
        self,
        mock_get_service,
        client,
        auth_headers,
        mock_session,
        mock_ptc_completed_response,
    ):
        """API should convert the public millisecond timeout contract to seconds."""
        mock_service = AsyncMock()
        mock_service.start_execution.return_value = mock_ptc_completed_response
        mock_get_service.return_value = mock_service

        from src.dependencies.services import get_session_service

        mock_session_svc = AsyncMock()
        mock_session_svc.create_session.return_value = mock_session
        app.dependency_overrides[get_session_service] = lambda: mock_session_svc

        try:
            response = client.post(
                "/exec/programmatic",
                json={
                    "code": "print('hello')",
                    "timeout": 60000,
                },
                headers=auth_headers,
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        assert mock_service.start_execution.await_args.kwargs["timeout"] == 60

    @patch("src.api.programmatic._get_ptc_service")
    def test_initial_request_accepts_librechat_file_refs(
        self,
        mock_get_service,
        client,
        auth_headers,
        mock_session,
        mock_ptc_completed_response,
    ):
        """API should parse the CodeEnvFile shape used by LibreChat agents."""
        mock_service = AsyncMock()
        mock_service.start_execution.return_value = mock_ptc_completed_response
        mock_get_service.return_value = mock_service

        from src.dependencies.services import get_session_service

        mock_session_svc = AsyncMock()
        mock_session_svc.create_session.return_value = mock_session
        app.dependency_overrides[get_session_service] = lambda: mock_session_svc

        try:
            response = client.post(
                "/exec/programmatic",
                json={
                    "code": "print('hello')",
                    "files": [
                        {
                            "session_id": "upload-session",
                            "id": "file-123",
                            "name": "report.csv",
                        }
                    ],
                },
                headers=auth_headers,
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        forwarded_files = mock_service.start_execution.await_args.kwargs["files"]
        assert len(forwarded_files) == 1
        assert forwarded_files[0].session_id == "upload-session"
        assert forwarded_files[0].id == "file-123"
        assert forwarded_files[0].name == "report.csv"


# =============================================================================
# CONTINUATION
# =============================================================================


class TestProgrammaticContinuation:
    """Tests for POST /exec/programmatic with continuation."""

    @patch("src.api.programmatic._get_ptc_service")
    def test_continuation_with_tool_results(
        self,
        mock_get_service,
        client,
        auth_headers,
        mock_ptc_completed_response,
    ):
        """Continuation with tool_results should return response."""
        mock_service = AsyncMock()
        mock_service.continue_execution.return_value = mock_ptc_completed_response
        mock_get_service.return_value = mock_service

        from src.dependencies.services import get_session_service

        mock_session_svc = AsyncMock()
        app.dependency_overrides[get_session_service] = lambda: mock_session_svc

        try:
            response = client.post(
                "/exec/programmatic",
                json={
                    "continuation_token": "cont-token-abc",
                    "tool_results": [
                        {
                            "call_id": "call-1",
                            "result": {"temp": 72, "conditions": "sunny"},
                        }
                    ],
                },
                headers=auth_headers,
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"

        mock_service.continue_execution.assert_called_once()

    @patch("src.api.programmatic._get_ptc_service")
    def test_continuation_invalid_token(
        self,
        mock_get_service,
        client,
        auth_headers,
        mock_ptc_error_response,
    ):
        """Continuation with invalid token should return error."""
        mock_service = AsyncMock()
        mock_service.continue_execution.return_value = mock_ptc_error_response
        mock_get_service.return_value = mock_service

        from src.dependencies.services import get_session_service

        mock_session_svc = AsyncMock()
        app.dependency_overrides[get_session_service] = lambda: mock_session_svc

        try:
            response = client.post(
                "/exec/programmatic",
                json={
                    "continuation_token": "invalid-token-xyz",
                    "tool_results": [{"call_id": "call-1", "result": "data"}],
                },
                headers=auth_headers,
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"
        assert "Invalid or expired" in data["error"]


# =============================================================================
# VALIDATION ERRORS
# =============================================================================


class TestProgrammaticValidation:
    """Tests for request validation on the PTC endpoint."""

    @patch("src.api.programmatic._get_ptc_service")
    def test_missing_code_returns_error(
        self, mock_get_service, client, auth_headers, mock_session
    ):
        """Request without code or continuation_token should return error."""
        mock_service = AsyncMock()
        mock_get_service.return_value = mock_service

        from src.dependencies.services import get_session_service

        mock_session_svc = AsyncMock()
        mock_session_svc.create_session.return_value = mock_session
        app.dependency_overrides[get_session_service] = lambda: mock_session_svc

        try:
            response = client.post(
                "/exec/programmatic",
                json={},
                headers=auth_headers,
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"
        assert (
            "code" in data["error"].lower() or "continuation" in data["error"].lower()
        )

    def test_invalid_json_returns_422(self, client, auth_headers):
        """Sending invalid JSON should return 422."""
        response = client.post(
            "/exec/programmatic",
            content="not-json",
            headers={**auth_headers, "content-type": "application/json"},
        )
        assert response.status_code == 422

    def test_timeout_below_minimum_returns_422(self, client, auth_headers):
        """The public timeout contract is milliseconds with a 1s minimum."""
        response = client.post(
            "/exec/programmatic",
            json={"code": "print('hello')", "timeout": 999},
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_legacy_inline_file_payload_returns_422(self, client, auth_headers):
        """PTC only accepts referenced CodeEnvFile payloads."""
        response = client.post(
            "/exec/programmatic",
            json={
                "code": "print('hello')",
                "files": [{"filename": "test.txt", "content": "data"}],
            },
            headers=auth_headers,
        )
        assert response.status_code == 422


# =============================================================================
# RESPONSE SCHEMA
# =============================================================================


class TestProgrammaticResponseSchema:
    """Tests for response schema compliance."""

    @patch("src.api.programmatic._get_ptc_service")
    def test_completed_response_has_expected_fields(
        self,
        mock_get_service,
        client,
        auth_headers,
        mock_session,
        mock_ptc_completed_response,
    ):
        """Completed response should have all expected fields."""
        mock_service = AsyncMock()
        mock_service.start_execution.return_value = mock_ptc_completed_response
        mock_get_service.return_value = mock_service

        from src.dependencies.services import get_session_service

        mock_session_svc = AsyncMock()
        mock_session_svc.create_session.return_value = mock_session
        app.dependency_overrides[get_session_service] = lambda: mock_session_svc

        try:
            response = client.post(
                "/exec/programmatic",
                json={"code": "print('hi')"},
                headers=auth_headers,
            )
        finally:
            app.dependency_overrides.clear()

        data = response.json()
        # All fields should be present in response
        assert "status" in data
        assert "session_id" in data
        assert "continuation_token" in data
        assert "tool_calls" in data
        assert "stdout" in data
        assert "stderr" in data
        assert "files" in data
        assert "error" in data

    @patch("src.api.programmatic._get_ptc_service")
    def test_tool_call_response_has_expected_fields(
        self,
        mock_get_service,
        client,
        auth_headers,
        mock_session,
        mock_ptc_tool_call_response,
    ):
        """Tool call response should have tool_calls with id, name, input."""
        mock_service = AsyncMock()
        mock_service.start_execution.return_value = mock_ptc_tool_call_response
        mock_get_service.return_value = mock_service

        from src.dependencies.services import get_session_service

        mock_session_svc = AsyncMock()
        mock_session_svc.create_session.return_value = mock_session
        app.dependency_overrides[get_session_service] = lambda: mock_session_svc

        try:
            response = client.post(
                "/exec/programmatic",
                json={
                    "code": "await get_weather(city='NYC')",
                    "tools": [{"name": "get_weather"}],
                },
                headers=auth_headers,
            )
        finally:
            app.dependency_overrides.clear()

        data = response.json()
        assert data["status"] == "tool_call_required"
        assert len(data["tool_calls"]) > 0
        tool_call = data["tool_calls"][0]
        assert "id" in tool_call
        assert "name" in tool_call
        assert "input" in tool_call


# =============================================================================
# AUTHENTICATION
# =============================================================================


class TestProgrammaticAuth:
    """Tests for authentication on the PTC endpoint."""

    def test_missing_auth_returns_401(self, client):
        """Request without auth headers should return 401."""
        response = client.post(
            "/exec/programmatic",
            json={"code": "print('hello')"},
        )
        assert response.status_code == 401

    def test_invalid_auth_returns_401(self, client):
        """Request with invalid API key should return 401."""
        response = client.post(
            "/exec/programmatic",
            json={"code": "print('hello')"},
            headers={"x-api-key": "wrong-key"},
        )
        assert response.status_code == 401


class TestProgrammaticLangField:
    """Tests for the `lang` field on /exec/programmatic.

    LibreChat's BashProgrammaticToolCalling sends {lang: "bash", ...}; the
    Python tool sends nothing (default). Invalid languages must be rejected
    so silent Python execution doesn't surprise callers."""

    @patch("src.api.programmatic._get_ptc_service")
    def test_lang_defaults_to_py(
        self,
        mock_get_service,
        client,
        auth_headers,
        mock_session,
        mock_ptc_completed_response,
    ):
        mock_service = AsyncMock()
        mock_service.start_execution.return_value = mock_ptc_completed_response
        mock_get_service.return_value = mock_service

        from src.dependencies.services import get_session_service

        mock_session_svc = AsyncMock()
        mock_session_svc.create_session.return_value = mock_session
        app.dependency_overrides[get_session_service] = lambda: mock_session_svc

        try:
            response = client.post(
                "/exec/programmatic",
                json={"code": "print('hi')"},
                headers=auth_headers,
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        assert mock_service.start_execution.await_args.kwargs["lang"] == "py"

    @patch("src.api.programmatic._get_ptc_service")
    def test_lang_bash_routed_to_service(
        self,
        mock_get_service,
        client,
        auth_headers,
        mock_session,
        mock_ptc_completed_response,
    ):
        mock_service = AsyncMock()
        mock_service.start_execution.return_value = mock_ptc_completed_response
        mock_get_service.return_value = mock_service

        from src.dependencies.services import get_session_service

        mock_session_svc = AsyncMock()
        mock_session_svc.create_session.return_value = mock_session
        app.dependency_overrides[get_session_service] = lambda: mock_session_svc

        try:
            response = client.post(
                "/exec/programmatic",
                json={"code": "echo hello", "lang": "bash", "tools": []},
                headers=auth_headers,
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        assert mock_service.start_execution.await_args.kwargs["lang"] == "bash"

    def test_invalid_lang_returns_422(self, client, auth_headers):
        response = client.post(
            "/exec/programmatic",
            json={"code": "puts 'hi'", "lang": "ruby"},
            headers=auth_headers,
        )
        assert response.status_code == 422
