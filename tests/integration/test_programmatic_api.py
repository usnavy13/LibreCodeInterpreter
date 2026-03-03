"""Integration tests for the Programmatic Tool Calling (PTC) API endpoint.

Tests use TestClient with mocked ProgrammaticService to verify the API
contract without requiring actual sandbox infrastructure.
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
