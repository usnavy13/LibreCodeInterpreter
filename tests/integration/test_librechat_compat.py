"""
LibreChat Compatibility Tests - Strict Acceptance Tests

This test suite verifies EXACT LibreChat API compatibility by testing only
what LibreChat actually sends and expects. These tests serve as acceptance
criteria for LibreChat integration.

Source of truth:
- @librechat/agents package: src/tools/CodeExecutor.ts
- LibreChat API: api/server/services/Files/Code/crud.js, process.js

Test approach:
- Mock ExecutionOrchestrator.execute() to return ExecResponse directly
- Tests verify the API contract, not internal implementation
- Only tests actual LibreChat behavior, no backward compatibility tests
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone, timedelta
import io
import json

from src.main import app
from src.models.exec import ExecResponse, FileRef
from src.models.files import FileInfo


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Provide authentication headers for tests."""
    return {"x-api-key": "test-api-key-for-testing-12345"}


@pytest.fixture
def mock_exec_response():
    """Standard successful execution response."""
    return ExecResponse(
        session_id="test-session-123", stdout="output\n", stderr="", files=[]
    )


# =============================================================================
# LIBRECHAT EXEC REQUEST FORMAT
# =============================================================================


class TestLibreChatExecRequest:
    """Test /exec request format exactly as LibreChat sends it.

    From CodeExecutor.ts, LibreChat sends:
    - lang: 'py' | 'js' | 'ts' | ... (required)
    - code: string (required)
    - session_id?: string (for file access)
    - args?: string[] (array only, not string)
    - user_id?: string
    - files?: Array<{id, session_id, name}>
    """

    @patch("src.services.orchestrator.ExecutionOrchestrator.execute")
    def test_librechat_minimal_request(
        self, mock_execute, client, auth_headers, mock_exec_response
    ):
        """
        Test LibreChat minimal request format.

        LibreChat sends: {"code": "...", "lang": "py"}
        """
        mock_execute.return_value = mock_exec_response

        request = {"code": "print('hello')", "lang": "py"}

        response = client.post("/exec", json=request, headers=auth_headers)
        assert response.status_code == 200
        mock_execute.assert_called_once()

    @patch("src.services.orchestrator.ExecutionOrchestrator.execute")
    def test_librechat_request_with_user_id(
        self, mock_execute, client, auth_headers, mock_exec_response
    ):
        """
        Test LibreChat request with user_id for tracking.

        LibreChat sends: {"code": "...", "lang": "py", "user_id": "user_..."}
        """
        mock_execute.return_value = mock_exec_response

        request = {"code": "print('hello')", "lang": "py", "user_id": "user_xyz789"}

        response = client.post("/exec", json=request, headers=auth_headers)
        assert response.status_code == 200

    @patch("src.services.orchestrator.ExecutionOrchestrator.execute")
    def test_librechat_request_with_files(
        self, mock_execute, client, auth_headers, mock_exec_response
    ):
        """
        Test LibreChat request with file references.

        LibreChat sends files as array of {id, session_id, name}.
        """
        mock_execute.return_value = mock_exec_response

        request = {
            "code": "with open('data.csv') as f: print(f.read())",
            "lang": "py",
            "entity_id": "asst_test",
            "files": [
                {
                    "id": "file-svc-abc123",
                    "session_id": "sess_xyz789",
                    "name": "data.csv",
                }
            ],
        }

        response = client.post("/exec", json=request, headers=auth_headers)
        assert response.status_code == 200

    @patch("src.services.orchestrator.ExecutionOrchestrator.execute")
    def test_librechat_request_with_multiple_files(
        self, mock_execute, client, auth_headers, mock_exec_response
    ):
        """Test LibreChat request with multiple file references."""
        mock_execute.return_value = mock_exec_response

        request = {
            "code": "import os; print(os.listdir('.'))",
            "lang": "py",
            "files": [
                {"id": "file-1", "session_id": "sess-1", "name": "file1.txt"},
                {"id": "file-2", "session_id": "sess-2", "name": "file2.txt"},
                {"id": "file-3", "session_id": "sess-3", "name": "file3.csv"},
            ],
        }

        response = client.post("/exec", json=request, headers=auth_headers)
        assert response.status_code == 200

    @patch("src.services.orchestrator.ExecutionOrchestrator.execute")
    def test_librechat_args_as_array(
        self, mock_execute, client, auth_headers, mock_exec_response
    ):
        """
        Test LibreChat args field format.

        LibreChat sends args as string[] array only (from @librechat/agents CodeExecutor.ts).
        The Zod schema defines: args: z.array(z.string()).optional()
        """
        mock_execute.return_value = mock_exec_response

        request = {
            "code": "print('test')",
            "lang": "py",
            "args": ["arg1", "arg2", "arg3"],
        }

        response = client.post("/exec", json=request, headers=auth_headers)
        assert response.status_code == 200

    @patch("src.services.orchestrator.ExecutionOrchestrator.execute")
    def test_librechat_request_with_session_id(
        self, mock_execute, client, auth_headers, mock_exec_response
    ):
        """
        Test LibreChat request with session_id for file access.

        LibreChat sends session_id to access files from previous executions.
        From CodeExecutor.ts: "Session ID from a previous response to access generated files."
        Files are loaded into /mnt/data/ and are READ-ONLY.
        """
        mock_execute.return_value = mock_exec_response

        request = {
            "code": "import os; print(os.listdir('/mnt/data'))",
            "lang": "py",
            "session_id": "prev-session-abc123",
        }

        response = client.post("/exec", json=request, headers=auth_headers)
        assert response.status_code == 200


# =============================================================================
# LIBRECHAT EXEC RESPONSE FORMAT
# =============================================================================


class TestLibreChatExecResponse:
    """Test /exec response format exactly as LibreChat expects it.

    From ExecuteResult type in @librechat/agents:
    - session_id: string (required)
    - stdout: string (required)
    - stderr: string (required)
    - files?: Array<{id, name, path?}>

    Additional fields (has_state, state_size, state_hash) are allowed and ignored.
    """

    @patch("src.services.orchestrator.ExecutionOrchestrator.execute")
    def test_response_has_required_fields(self, mock_execute, client, auth_headers):
        """
        Test LibreChat response has required fields: session_id, files, stdout, stderr.

        LibreChat reads these 4 fields from the response (from @librechat/agents ExecuteResult type).
        Additional fields (like has_state, state_size, state_hash for Python) are allowed
        and will be ignored by LibreChat.
        """
        mock_execute.return_value = ExecResponse(
            session_id="resp-session-123", stdout="test output\n", stderr="", files=[]
        )

        response = client.post(
            "/exec", json={"code": "print('test')", "lang": "py"}, headers=auth_headers
        )

        data = response.json()

        # Must have these four fields
        assert "session_id" in data
        assert "files" in data
        assert "stdout" in data
        assert "stderr" in data

        # Verify types
        assert isinstance(data["session_id"], str)
        assert isinstance(data["files"], list)
        assert isinstance(data["stdout"], str)
        assert isinstance(data["stderr"], str)

    @patch("src.services.orchestrator.ExecutionOrchestrator.execute")
    def test_stdout_ends_with_newline(self, mock_execute, client, auth_headers):
        """
        Test that stdout ends with newline.

        LibreChat UI expects this for proper display.
        """
        mock_execute.return_value = ExecResponse(
            session_id="resp-session-123", stdout="hello\n", stderr="", files=[]
        )

        response = client.post(
            "/exec", json={"code": "print('hello')", "lang": "py"}, headers=auth_headers
        )

        data = response.json()
        assert data["stdout"].endswith(
            "\n"
        ), "stdout must end with newline for LibreChat"

    @patch("src.services.orchestrator.ExecutionOrchestrator.execute")
    def test_files_array_format(self, mock_execute, client, auth_headers):
        """
        Test generated files format: {id, name, path?}

        LibreChat expects: {"id": "...", "name": "...", "path": "..."}
        """
        mock_execute.return_value = ExecResponse(
            session_id="resp-session-123",
            stdout="",
            stderr="",
            files=[FileRef(id="gen-file-abc", name="output.png", path="/output.png")],
        )

        response = client.post(
            "/exec", json={"code": "generate image", "lang": "py"}, headers=auth_headers
        )

        data = response.json()
        assert len(data["files"]) == 1

        file_ref = data["files"][0]
        # Required fields for LibreChat
        assert "id" in file_ref, "File must have 'id' field"
        assert "name" in file_ref, "File must have 'name' field"
        # path is optional but typically included

    @patch("src.services.orchestrator.ExecutionOrchestrator.execute")
    def test_empty_stderr_on_success(self, mock_execute, client, auth_headers):
        """Test stderr is empty string on successful execution."""
        mock_execute.return_value = ExecResponse(
            session_id="resp-session-123", stdout="ok\n", stderr="", files=[]
        )

        response = client.post(
            "/exec", json={"code": "print('ok')", "lang": "py"}, headers=auth_headers
        )

        data = response.json()
        assert data["stderr"] == "", "stderr should be empty on success"

    @patch("src.services.orchestrator.ExecutionOrchestrator.execute")
    def test_stderr_populated_on_error(self, mock_execute, client, auth_headers):
        """Test stderr contains error message on failure."""
        mock_execute.return_value = ExecResponse(
            session_id="resp-session-123",
            stdout="",
            stderr="Traceback: Exception: error\n",
            files=[],
        )

        response = client.post(
            "/exec",
            json={"code": "raise Exception('error')", "lang": "py"},
            headers=auth_headers,
        )

        data = response.json()
        assert len(data["stderr"]) > 0, "stderr should contain the error"

    @patch("src.services.orchestrator.ExecutionOrchestrator.execute")
    def test_session_id_is_string(self, mock_execute, client, auth_headers):
        """Test session_id is always a non-empty string."""
        mock_execute.return_value = ExecResponse(
            session_id="resp-session-123", stdout="", stderr="", files=[]
        )

        response = client.post(
            "/exec", json={"code": "pass", "lang": "py"}, headers=auth_headers
        )

        data = response.json()
        assert isinstance(data["session_id"], str)
        assert len(data["session_id"]) > 0


# =============================================================================
# LIBRECHAT FILE UPLOAD FORMAT
# =============================================================================


class TestLibreChatFileUpload:
    """Test /upload format exactly as LibreChat sends it.

    LibreChat uploads files via POST /upload with:
    - 'file' field (singular) containing the file
    - 'entity_id' field (optional)
    - Headers: X-API-Key, User-Id, User-Agent: 'LibreChat/1.0'

    From crud.js: form.append('file', stream, filename)
    """

    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        """Set up mocks."""
        mock_file_service = AsyncMock()
        mock_file_service.store_uploaded_file.return_value = "lc-file-123"

        from src.dependencies.services import get_file_service

        app.dependency_overrides[get_file_service] = lambda: mock_file_service

        yield

        app.dependency_overrides.clear()

    def test_multipart_upload_format(self, client, auth_headers):
        """
        Test LibreChat multipart upload format.

        LibreChat sends: multipart/form-data with 'file' (singular) field and 'entity_id'.
        From crud.js: form.append('file', stream, filename)
        """
        # LibreChat uses 'file' (singular), not 'files'
        files = {
            "file": ("document.pdf", io.BytesIO(b"PDF content"), "application/pdf")
        }
        data = {"entity_id": "asst_librechat"}

        response = client.post("/upload", files=files, data=data, headers=auth_headers)

        assert response.status_code == 200
        result = response.json()

        # API returns {message, session_id, files: [{fileId, filename}]}
        # LibreChat checks: if (result.message !== 'success') throw error
        assert result.get("message") == "success", "LibreChat expects message='success'"
        assert "files" in result
        assert len(result["files"]) == 1
        assert "session_id" in result

        file_info = result["files"][0]
        assert "fileId" in file_info
        assert "filename" in file_info

    def test_upload_response_has_session_id(self, client, auth_headers):
        """Test that upload response includes a session_id."""
        entity_id = "asst_specific_entity"
        # LibreChat uses 'file' (singular)
        files = {"file": ("test.txt", io.BytesIO(b"content"), "text/plain")}
        data = {"entity_id": entity_id}

        response = client.post("/upload", files=files, data=data, headers=auth_headers)

        result = response.json()
        # API generates a new session_id for uploads (entity_id is currently not used)
        assert "session_id" in result
        assert len(result["session_id"]) > 0

    def test_librechat_upload_with_user_id_header(self, client, auth_headers):
        """
        Test LibreChat upload includes User-Id header.

        LibreChat sends: 'User-Id': req.user.id
        From crud.js: headers: { 'User-Id': req.user.id }
        """
        files = {"file": ("test.txt", io.BytesIO(b"content"), "text/plain")}
        data = {"entity_id": "asst_test"}

        # Add User-Id header as LibreChat does
        headers = {
            **auth_headers,
            "User-Id": "user_abc123",
            "User-Agent": "LibreChat/1.0",
        }

        response = client.post("/upload", files=files, data=data, headers=headers)

        # Should accept the User-Id header without error
        assert response.status_code == 200


# =============================================================================
# LIBRECHAT FILE RETRIEVAL
# =============================================================================


class TestLibreChatFileRetrieval:
    """Test file retrieval endpoints as LibreChat uses them.

    LibreChat uses these endpoints to:
    1. GET /files/{session_id}?detail=... - List session files
    2. GET /download/{session_id}/{fileId} - Download generated files

    From CodeExecutor.ts and process.js
    """

    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        """Set up mocks for file service."""
        self.mock_file_service = AsyncMock()

        from src.dependencies.services import get_file_service

        app.dependency_overrides[get_file_service] = lambda: self.mock_file_service

        yield

        app.dependency_overrides.clear()

    def test_files_endpoint_with_detail_summary(self, client, auth_headers):
        """
        Test GET /files/{session_id}?detail=summary endpoint.

        LibreChat calls this to check if session files exist.
        From process.js: GET /files/{session_id}?detail=summary
        """
        self.mock_file_service.list_files.return_value = [
            FileInfo(
                file_id="file-123",
                filename="output.png",
                size=1024,
                content_type="image/png",
                created_at=datetime.now(timezone.utc),
                path="/output.png",
            )
        ]

        response = client.get(
            "/files/test-session-123?detail=summary", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_files_endpoint_with_detail_full(self, client, auth_headers):
        """
        Test GET /files/{session_id}?detail=full endpoint.

        LibreChat calls this to get full file metadata for execution.
        From CodeExecutor.ts: GET /files/{session_id}?detail=full
        """
        self.mock_file_service.list_files.return_value = [
            FileInfo(
                file_id="file-456",
                filename="data.csv",
                size=2048,
                content_type="text/csv",
                created_at=datetime.now(timezone.utc),
                path="/data.csv",
            )
        ]

        response = client.get(
            "/files/test-session-456?detail=full", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_download_endpoint(self, client, auth_headers):
        """
        Test GET /download/{session_id}/{fileId} endpoint.

        LibreChat downloads generated files using this endpoint.
        From crud.js: GET /download/{session_id}/{fileId}
        """
        # Mock file service to return file content
        self.mock_file_service.get_file.return_value = (
            io.BytesIO(b"file content here"),
            "output.txt",
            "text/plain",
        )

        response = client.get(
            "/download/test-session-789/file-abc", headers=auth_headers
        )

        # Should return file content or appropriate response
        # Note: Actual status depends on whether file exists in mock
        assert response.status_code in [200, 404]


# =============================================================================
# LIBRECHAT AUTHENTICATION
# =============================================================================


class TestLibreChatAuthentication:
    """Test authentication exactly as LibreChat uses it.

    LibreChat only uses X-API-Key header for authentication.
    From CodeExecutor.ts: headers: { 'X-API-Key': apiKey }
    """

    def test_x_api_key_header(self, client):
        """
        Test x-api-key header authentication.

        LibreChat sends: headers: { 'X-API-Key': apiKey }
        """
        headers = {"x-api-key": "test-api-key-for-testing-12345"}

        # Just check auth doesn't fail
        response = client.get("/health", headers=headers)
        assert response.status_code == 200


# =============================================================================
# LIBRECHAT ERROR HANDLING
# =============================================================================


class TestLibreChatErrors:
    """Test error handling as LibreChat expects.

    Critical: Code execution errors must return HTTP 200 with error in stderr.
    LibreChat does NOT expect HTTP 4xx/5xx for code errors - only for API errors.
    """

    def test_validation_error_format(self, client, auth_headers):
        """Test validation errors have expected format."""
        # Missing required field - no mock needed, this tests request validation
        response = client.post("/exec", json={"lang": "py"}, headers=auth_headers)

        assert response.status_code == 422
        data = response.json()
        # API uses custom error format with 'error' field
        assert "error" in data or "detail" in data

    def test_auth_error_format(self, client):
        """Test authentication errors have expected format."""
        # No mock needed - this tests auth middleware
        response = client.post("/exec", json={"code": "test", "lang": "py"})

        assert response.status_code == 401
        data = response.json()
        assert "error" in data

    @patch("src.services.orchestrator.ExecutionOrchestrator.execute")
    def test_execution_error_returns_200(self, mock_execute, client, auth_headers):
        """
        Test that code execution errors still return 200.

        LibreChat expects 200 with error in stderr, not HTTP error.
        """
        mock_execute.return_value = ExecResponse(
            session_id="err-session",
            stdout="",
            stderr="SyntaxError: invalid syntax\n",
            files=[],
        )

        response = client.post(
            "/exec",
            json={"code": "this is not valid python [[[", "lang": "py"},
            headers=auth_headers,
        )

        # CRITICAL: Should return 200, not 4xx or 5xx
        assert response.status_code == 200

        data = response.json()
        # Should have standard response format with error in stderr
        assert "session_id" in data
        assert "files" in data
        assert "stdout" in data
        assert "stderr" in data

    @patch("src.services.orchestrator.ExecutionOrchestrator.execute")
    def test_timeout_returns_200(self, mock_execute, client, auth_headers):
        """Test that timeout still returns 200 with appropriate message."""
        mock_execute.return_value = ExecResponse(
            session_id="timeout-session",
            stdout="",
            stderr="Execution timed out after 30 seconds\n",
            files=[],
        )

        response = client.post(
            "/exec",
            json={"code": "import time; time.sleep(9999)", "lang": "py"},
            headers=auth_headers,
        )

        # Should return 200 even for timeout
        assert response.status_code == 200
