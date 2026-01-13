"""Integration tests for the /state endpoints."""

import base64
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock

from src.main import app
from src.services.state import StateService
from src.services.state_archival import StateArchivalService
from src.dependencies.services import get_state_service, get_state_archival_service


@pytest.fixture
def mock_state_service():
    """Mock state service for testing."""
    service = AsyncMock(spec=StateService)
    service.get_state_hash = AsyncMock(return_value=None)
    service.get_state_raw = AsyncMock(return_value=None)
    service.get_state = AsyncMock(return_value=None)
    service.get_full_state_info = AsyncMock(return_value=None)
    service.save_state_raw = AsyncMock(return_value=True)
    service.delete_state = AsyncMock(return_value=True)
    service.has_recent_upload = AsyncMock(return_value=False)
    service.clear_upload_marker = AsyncMock()
    service.compute_hash = StateService.compute_hash  # Use real static method
    return service


@pytest.fixture
def mock_state_archival_service():
    """Mock state archival service for testing."""
    service = AsyncMock(spec=StateArchivalService)
    service.restore_state = AsyncMock(return_value=None)
    service.has_archived_state = AsyncMock(return_value=False)
    service.delete_archived_state = AsyncMock()
    return service


@pytest.fixture
def client(mock_state_service, mock_state_archival_service):
    """Create test client with mocked services."""
    # Override dependencies
    app.dependency_overrides[get_state_service] = lambda: mock_state_service
    app.dependency_overrides[get_state_archival_service] = (
        lambda: mock_state_archival_service
    )

    client = TestClient(app)
    yield client

    # Cleanup overrides
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers():
    """Provide authentication headers for tests."""
    return {"x-api-key": "test-api-key-for-testing-12345"}


class TestDownloadState:
    """Tests for GET /state/{session_id}."""

    def test_download_nonexistent_state_returns_404(
        self, client, auth_headers, mock_state_service
    ):
        """Test that downloading nonexistent state returns 404."""
        # mock_state_service already returns None by default
        response = client.get("/state/nonexistent-session", headers=auth_headers)

        assert response.status_code == 404
        data = response.json()
        # Error handler stringifies the detail dict
        assert "state_not_found" in data["error"]

    def test_download_state_returns_etag(
        self, client, auth_headers, mock_state_service
    ):
        """Test that downloading state returns ETag header."""
        # Setup mock with state
        raw_bytes = b"\x02test state data"
        state_hash = StateService.compute_hash(raw_bytes)
        mock_state_service.get_state_hash.return_value = state_hash
        mock_state_service.get_state_raw.return_value = raw_bytes

        response = client.get("/state/test-session", headers=auth_headers)

        assert response.status_code == 200
        assert "ETag" in response.headers
        assert response.headers["ETag"] == f'"{state_hash}"'
        assert response.content == raw_bytes

    def test_if_none_match_returns_304(self, client, auth_headers, mock_state_service):
        """Test that If-None-Match with matching ETag returns 304."""
        raw_bytes = b"\x02cached state"
        state_hash = StateService.compute_hash(raw_bytes)
        mock_state_service.get_state_hash.return_value = state_hash

        headers = {**auth_headers, "If-None-Match": f'"{state_hash}"'}

        response = client.get("/state/test-session", headers=headers)

        assert response.status_code == 304


class TestUploadState:
    """Tests for POST /state/{session_id}."""

    def test_upload_valid_state_returns_201(
        self, client, auth_headers, mock_state_service
    ):
        """Test that uploading valid state returns 201."""
        # Create valid state blob (version 2 + some data)
        raw_bytes = b"\x02fake lz4 compressed data here"

        response = client.post(
            "/state/test-session",
            content=raw_bytes,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["message"] == "state_uploaded"
        assert data["size"] == len(raw_bytes)

    def test_upload_invalid_version_returns_400(
        self, client, auth_headers, mock_state_service
    ):
        """Test that invalid version byte returns 400."""
        # Version 99 is invalid
        raw_bytes = b"\x63invalid version data"

        response = client.post(
            "/state/test-session",
            content=raw_bytes,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        assert response.status_code == 400
        data = response.json()
        # Error handler stringifies the detail dict
        assert "invalid_state" in data["error"]

    def test_upload_too_short_returns_400(
        self, client, auth_headers, mock_state_service
    ):
        """Test that state < 2 bytes returns 400."""
        raw_bytes = b"\x02"  # Only 1 byte

        response = client.post(
            "/state/test-session",
            content=raw_bytes,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        assert response.status_code == 400


class TestGetStateInfo:
    """Tests for GET /state/{session_id}/info."""

    def test_info_nonexistent_returns_exists_false(
        self, client, auth_headers, mock_state_service
    ):
        """Test that info for nonexistent state returns exists=false."""
        response = client.get("/state/nonexistent/info", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["exists"] is False

    def test_info_existing_state_returns_metadata(
        self, client, auth_headers, mock_state_service
    ):
        """Test that info for existing state returns metadata."""
        mock_state_service.get_full_state_info.return_value = {
            "size_bytes": 1024,
            "hash": "abc123",
            "created_at": "2025-12-21T10:00:00+00:00",
            "expires_at": "2025-12-21T12:00:00+00:00",
        }

        response = client.get("/state/test-session/info", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["exists"] is True
        assert data["source"] == "redis"
        assert data["size_bytes"] == 1024

    def test_info_archived_state_returns_archive_source(
        self, client, auth_headers, mock_state_service, mock_state_archival_service
    ):
        """Test that archived state shows source='archive'."""
        mock_state_archival_service.has_archived_state.return_value = True

        response = client.get("/state/archived-session/info", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["exists"] is True
        assert data["source"] == "archive"


class TestDeleteState:
    """Tests for DELETE /state/{session_id}."""

    def test_delete_returns_204(self, client, auth_headers, mock_state_service):
        """Test that delete returns 204."""
        response = client.delete("/state/test-session", headers=auth_headers)

        assert response.status_code == 204

    def test_delete_nonexistent_still_returns_204(
        self, client, auth_headers, mock_state_service
    ):
        """Test that deleting nonexistent state still returns 204."""
        response = client.delete("/state/nonexistent", headers=auth_headers)

        assert response.status_code == 204


class TestExecResponseStateFields:
    """Tests for state fields in /exec response."""

    def test_exec_response_includes_state_fields_for_python(self, client, auth_headers):
        """Test that Python execution response includes state fields."""
        # This is a more complex integration test that requires full stack
        # For now, we test the model structure
        from src.models.exec import ExecResponse

        response = ExecResponse(
            session_id="test-session",
            stdout="output",
            stderr="",
            has_state=True,
            state_size=1024,
            state_hash="abc123",
        )

        assert response.has_state is True
        assert response.state_size == 1024
        assert response.state_hash == "abc123"

    def test_exec_response_defaults_state_fields(self):
        """Test that state fields have correct defaults."""
        from src.models.exec import ExecResponse

        response = ExecResponse(session_id="test-session", stdout="", stderr="")

        assert response.has_state is False
        assert response.state_size is None
        assert response.state_hash is None
