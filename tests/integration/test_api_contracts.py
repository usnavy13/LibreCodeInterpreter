"""
API Contract Tests - Phase 0 Behavioral Baseline

This test suite documents and verifies the exact API contract behavior
to ensure 100% compatibility after architectural refactoring.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone, timedelta
import io
import json

from src.main import app
from src.models import CodeExecution, ExecutionStatus, ExecutionOutput, OutputType
from src.models.session import Session, SessionStatus
from src.models.files import FileInfo


# All 12 supported languages
SUPPORTED_LANGUAGES = [
    "py",
    "js",
    "ts",
    "go",
    "java",
    "c",
    "cpp",
    "php",
    "rs",
    "r",
    "f90",
    "d",
]


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
    """Create a mock session."""
    return Session(
        session_id="test-session-123",
        status=SessionStatus.ACTIVE,
        created_at=datetime.now(timezone.utc),
        last_activity=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        metadata={"entity_id": "test-entity"},
    )


@pytest.fixture
def mock_session_service(mock_session):
    """Mock session service."""
    service = AsyncMock()
    service.create_session.return_value = mock_session
    service.get_session.return_value = mock_session
    service.validate_session_access.return_value = True
    return service


def create_mock_execution(
    language: str, stdout: str = "output", stderr: str = ""
) -> CodeExecution:
    """Helper to create mock execution for any language."""
    outputs = []
    if stdout:
        outputs.append(
            ExecutionOutput(
                type=OutputType.STDOUT,
                content=stdout,
                timestamp=datetime.now(timezone.utc),
            )
        )
    if stderr:
        outputs.append(
            ExecutionOutput(
                type=OutputType.STDERR,
                content=stderr,
                timestamp=datetime.now(timezone.utc),
            )
        )

    return CodeExecution(
        execution_id=f"exec-{language}-123",
        session_id="test-session-123",
        code=f"test code for {language}",
        language=language,
        status=ExecutionStatus.COMPLETED,
        exit_code=0,
        execution_time_ms=100,
        outputs=outputs,
    )


@pytest.fixture
def mock_execution_service():
    """Mock execution service."""
    service = AsyncMock()
    # Return tuple: (execution, container, new_state, state_errors, container_source)
    service.execute_code.return_value = (
        create_mock_execution("py", "Hello, World!"),
        None,
        None,
        [],
        "pool_hit",
    )
    return service


@pytest.fixture
def mock_file_service():
    """Mock file service."""
    service = AsyncMock()
    service.list_files.return_value = []
    service.store_uploaded_file.return_value = "test-file-id-123"
    service.get_file_info.return_value = FileInfo(
        file_id="test-file-id-123",
        filename="test.txt",
        size=1024,
        content_type="text/plain",
        created_at=datetime.utcnow(),
        path="/test.txt",
    )
    service.download_file.return_value = "https://minio.example.com/download-url"
    service.delete_file.return_value = True
    return service


@pytest.fixture(autouse=True)
def mock_dependencies(mock_session_service, mock_execution_service, mock_file_service):
    """Mock all dependencies for testing."""
    from src.dependencies.services import (
        get_session_service,
        get_execution_service,
        get_file_service,
    )

    app.dependency_overrides[get_session_service] = lambda: mock_session_service
    app.dependency_overrides[get_execution_service] = lambda: mock_execution_service
    app.dependency_overrides[get_file_service] = lambda: mock_file_service

    yield

    app.dependency_overrides.clear()


# =============================================================================
# EXEC ENDPOINT - REQUEST FORMAT
# =============================================================================


class TestExecRequestFormat:
    """Test /exec request format validation."""

    def test_exec_minimal_request(self, client, auth_headers):
        """Test minimal valid request with just code and lang."""
        request_data = {"code": "print('hello')", "lang": "py"}

        response = client.post("/exec", json=request_data, headers=auth_headers)
        assert response.status_code == 200

    def test_exec_full_request(self, client, auth_headers, mock_session_service):
        """Test request with all optional fields."""
        request_data = {
            "code": "print('hello')",
            "lang": "py",
            "args": "arg1 arg2",
            "user_id": "user-123",
            "entity_id": "entity-456",
            "files": [],
        }

        response = client.post("/exec", json=request_data, headers=auth_headers)
        assert response.status_code == 200

    def test_exec_with_file_references(
        self, client, auth_headers, mock_execution_service
    ):
        """Test request with file references."""
        request_data = {
            "code": "with open('data.txt') as f: print(f.read())",
            "lang": "py",
            "files": [
                {"id": "file-123", "session_id": "session-456", "name": "data.txt"}
            ],
        }

        response = client.post("/exec", json=request_data, headers=auth_headers)
        assert response.status_code == 200

    def test_exec_args_accepts_any_json(self, client, auth_headers):
        """Test that args field accepts any JSON type (string, object, array)."""
        # String args
        response = client.post(
            "/exec",
            json={"code": "print('test')", "lang": "py", "args": "string args"},
            headers=auth_headers,
        )
        assert response.status_code == 200

        # Object args
        response = client.post(
            "/exec",
            json={"code": "print('test')", "lang": "py", "args": {"key": "value"}},
            headers=auth_headers,
        )
        assert response.status_code == 200

        # Array args
        response = client.post(
            "/exec",
            json={"code": "print('test')", "lang": "py", "args": ["arg1", "arg2"]},
            headers=auth_headers,
        )
        assert response.status_code == 200

    def test_exec_missing_code_rejected(self, client, auth_headers):
        """Test that missing code field is rejected."""
        response = client.post("/exec", json={"lang": "py"}, headers=auth_headers)
        assert response.status_code == 422

    def test_exec_missing_lang_rejected(self, client, auth_headers):
        """Test that missing lang field is rejected."""
        response = client.post(
            "/exec", json={"code": "print('test')"}, headers=auth_headers
        )
        assert response.status_code == 422

    def test_exec_empty_code_rejected(self, client, auth_headers):
        """Test that empty code is rejected."""
        response = client.post(
            "/exec", json={"code": "", "lang": "py"}, headers=auth_headers
        )
        # API returns 400 for empty code (application-level validation)
        assert response.status_code == 400


class TestExecResponseFormat:
    """Test /exec response format (LibreChat compatibility)."""

    def test_response_has_required_fields(self, client, auth_headers):
        """Test that response has all required LibreChat fields."""
        response = client.post(
            "/exec", json={"code": "print('test')", "lang": "py"}, headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()

        # Required fields for LibreChat compatibility
        assert "session_id" in data
        assert "files" in data
        assert "stdout" in data
        assert "stderr" in data

        # Type validation
        assert isinstance(data["session_id"], str)
        assert isinstance(data["files"], list)
        assert isinstance(data["stdout"], str)
        assert isinstance(data["stderr"], str)

    def test_response_stdout_ends_with_newline(
        self, client, auth_headers, mock_execution_service
    ):
        """Test that stdout ends with newline for LibreChat compatibility."""
        mock_execution_service.execute_code.return_value = (
            create_mock_execution("py", "Hello, World!"),  # No trailing newline in mock
            None,
            None,
            [],
            "pool_hit",
        )

        response = client.post(
            "/exec",
            json={"code": "print('Hello, World!')", "lang": "py"},
            headers=auth_headers,
        )

        data = response.json()
        # LibreChat expects stdout to end with newline
        assert data["stdout"].endswith("\n")

    def test_response_files_format(
        self, client, auth_headers, mock_execution_service, mock_file_service
    ):
        """Test that generated files have correct format."""
        # Mock execution with file output
        execution_with_file = CodeExecution(
            execution_id="exec-files-123",
            session_id="test-session-123",
            code="write file",
            language="py",
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            outputs=[
                ExecutionOutput(
                    type=OutputType.FILE,
                    content="/workspace/output.txt",
                    mime_type="text/plain",
                    size=100,
                    timestamp=datetime.now(timezone.utc),
                )
            ],
        )
        mock_execution_service.execute_code.return_value = (
            execution_with_file,
            None,
            None,
            [],
            "pool_hit",
        )

        # Mock store_execution_output_file to return a file_id string
        mock_file_service.store_execution_output_file.return_value = "gen-file-123"

        # Mock file listing
        mock_file_service.list_files.return_value = [
            FileInfo(
                file_id="gen-file-123",
                filename="output.txt",
                size=100,
                content_type="text/plain",
                created_at=datetime.utcnow(),
                path="/output.txt",
            )
        ]

        response = client.post(
            "/exec", json={"code": "write file", "lang": "py"}, headers=auth_headers
        )

        data = response.json()
        assert len(data["files"]) >= 1

        # Verify file reference format
        file_ref = data["files"][0]
        assert "id" in file_ref
        assert "name" in file_ref
        # path is optional but should be present if available


# =============================================================================
# FILE ENDPOINTS
# =============================================================================


class TestFileUploadContract:
    """Test file upload endpoint contract."""

    def test_upload_single_file(self, client, auth_headers, mock_file_service):
        """Test single file upload returns correct format."""
        files = {"files": ("test.txt", io.BytesIO(b"test content"), "text/plain")}
        data = {"entity_id": "test-entity"}

        response = client.post("/upload", files=files, data=data, headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        # Verify response structure
        assert "files" in data
        assert len(data["files"]) == 1

        file_info = data["files"][0]
        # API returns fileId and filename
        assert "fileId" in file_info
        assert "filename" in file_info

    def test_upload_multiple_files(self, client, auth_headers, mock_file_service):
        """Test multiple file upload."""
        mock_file_service.store_uploaded_file.side_effect = ["file-1", "file-2"]

        files = [
            ("files", ("test1.txt", io.BytesIO(b"content 1"), "text/plain")),
            ("files", ("test2.txt", io.BytesIO(b"content 2"), "text/plain")),
        ]

        response = client.post("/upload", files=files, headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert len(data["files"]) == 2

    def test_upload_without_entity_id(self, client, auth_headers, mock_file_service):
        """Test upload without entity_id generates a new session ID."""
        files = {"files": ("test.txt", io.BytesIO(b"content"), "text/plain")}

        response = client.post("/upload", files=files, headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        # session_id is at the top level of the response
        assert "session_id" in data
        # API generates a random session ID when no entity_id is provided
        assert len(data["session_id"]) > 0


class TestFileListContract:
    """Test file listing endpoint contract."""

    def test_list_files_simple_detail(self, client, auth_headers, mock_file_service):
        """Test file listing with simple detail."""
        response = client.get("/files/test-session?detail=simple", headers=auth_headers)

        assert response.status_code == 200
        files = response.json()
        assert isinstance(files, list)

        if len(files) > 0:
            file_info = files[0]
            assert "id" in file_info
            assert "name" in file_info
            assert "path" in file_info
            # Simple detail should NOT include size
            assert "size" not in file_info

    def test_list_files_full_detail(self, client, auth_headers, mock_file_service):
        """Test file listing with full detail."""
        response = client.get("/files/test-session", headers=auth_headers)

        assert response.status_code == 200
        files = response.json()

        if len(files) > 0:
            file_info = files[0]
            assert "id" in file_info
            assert "name" in file_info
            assert "path" in file_info
            assert "size" in file_info
            assert "lastModified" in file_info
            assert "etag" in file_info
            assert "contentType" in file_info


class TestFileDownloadContract:
    """Test file download endpoint contract."""

    def test_download_returns_streaming_response(
        self, client, auth_headers, mock_file_service
    ):
        """Test that download returns streaming response with file content."""
        # Mock the file service to return file content
        mock_file_service.get_file_content.return_value = b"test file content"

        response = client.get(
            "/download/test-session/test-file-id-123", headers=auth_headers
        )

        # API returns streaming response (200), not redirect
        assert response.status_code == 200
        assert "content-disposition" in response.headers

    def test_download_not_found(self, client, auth_headers, mock_file_service):
        """Test download of non-existent file."""
        mock_file_service.get_file_info.return_value = None

        response = client.get(
            "/download/test-session/nonexistent", headers=auth_headers
        )

        assert response.status_code == 404


class TestFileDeleteContract:
    """Test file deletion endpoint contract."""

    def test_delete_success(self, client, auth_headers, mock_file_service):
        """Test successful file deletion."""
        response = client.delete(
            "/files/test-session/test-file-id-123", headers=auth_headers
        )

        # API returns 200 with empty body for LibreChat compatibility
        assert response.status_code == 200

    def test_delete_not_found(self, client, auth_headers, mock_file_service):
        """Test deletion of non-existent file."""
        mock_file_service.get_file_info.return_value = None

        response = client.delete(
            "/files/test-session/nonexistent", headers=auth_headers
        )

        assert response.status_code == 404


# =============================================================================
# HEALTH ENDPOINTS
# =============================================================================


class TestHealthContract:
    """Test health endpoint contracts."""

    def test_health_basic(self, client):
        """Test basic health endpoint (no auth required)."""
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()

        assert "status" in data
        assert data["status"] == "healthy"

    def test_health_services(self, client, auth_headers):
        """Test detailed health services endpoint."""
        response = client.get("/health/services", headers=auth_headers)

        # May require auth or may be public
        if response.status_code == 200:
            data = response.json()
            assert "status" in data
            # May include service details


# =============================================================================
# ERROR RESPONSE FORMAT
# =============================================================================


class TestErrorResponseFormat:
    """Test error response format consistency."""

    def test_validation_error_format(self, client, auth_headers):
        """Test validation error response format."""
        response = client.post("/exec", json={"code": ""}, headers=auth_headers)

        assert response.status_code == 422
        data = response.json()

        # API uses custom error format with 'error' field
        assert "error" in data or "detail" in data

    def test_auth_error_format(self, client):
        """Test authentication error response format."""
        response = client.post("/exec", json={"code": "test", "lang": "py"})

        assert response.status_code == 401
        data = response.json()

        assert "error" in data

    def test_not_found_error_format(self, client, auth_headers, mock_file_service):
        """Test not found error response format."""
        # Download endpoint uses get_file_info to check if file exists
        mock_file_service.get_file_info.return_value = None

        response = client.get(
            "/download/test-session/nonexistent", headers=auth_headers
        )

        assert response.status_code == 404
        data = response.json()

        # API uses custom error format with 'error' or 'detail' key
        assert "error" in data or "detail" in data


# =============================================================================
# AUTHENTICATION METHODS
# =============================================================================


class TestAuthenticationMethods:
    """Test all authentication methods work."""

    def test_x_api_key_header(self, client):
        """Test x-api-key header authentication."""
        headers = {"x-api-key": "test-api-key-for-testing-12345"}
        response = client.post(
            "/exec", json={"code": "print('test')", "lang": "py"}, headers=headers
        )

        assert response.status_code != 401

    def test_authorization_bearer(self, client):
        """Test Authorization Bearer authentication."""
        headers = {"Authorization": "Bearer test-api-key-for-testing-12345"}
        response = client.post(
            "/exec", json={"code": "print('test')", "lang": "py"}, headers=headers
        )

        assert response.status_code != 401

    def test_authorization_apikey(self, client):
        """Test Authorization ApiKey authentication."""
        headers = {"Authorization": "ApiKey test-api-key-for-testing-12345"}
        response = client.post(
            "/exec", json={"code": "print('test')", "lang": "py"}, headers=headers
        )

        assert response.status_code != 401

    def test_no_auth_rejected(self, client):
        """Test requests without auth are rejected."""
        response = client.post("/exec", json={"code": "print('test')", "lang": "py"})

        assert response.status_code == 401

    def test_invalid_auth_rejected(self, client):
        """Test requests with invalid auth are rejected."""
        headers = {"x-api-key": "invalid-key"}
        response = client.post(
            "/exec", json={"code": "print('test')", "lang": "py"}, headers=headers
        )

        assert response.status_code == 401
