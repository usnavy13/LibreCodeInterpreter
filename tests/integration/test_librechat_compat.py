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
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta
import concurrent.futures
import io
import json

from src.main import app
from src.models.exec import ExecResponse, FileRef
from src.models.files import FileInfo
from src.models.session import Session, SessionStatus


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
        mock_file_service.validate_uploads = MagicMock(return_value=None)

        mock_session_service = AsyncMock()
        mock_session_service.create_session.return_value = Session(
            session_id="upload-session-123",
            status=SessionStatus.ACTIVE,
            created_at=datetime.now(timezone.utc),
            last_activity=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
            metadata={},
        )

        from src.dependencies.services import get_file_service, get_session_service

        app.dependency_overrides[get_file_service] = lambda: mock_file_service
        app.dependency_overrides[get_session_service] = lambda: mock_session_service

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
        assert len(data) == 1
        item = data[0]
        assert "name" in item, "Summary must have 'name' field"
        assert "lastModified" in item, "Summary must have 'lastModified' field"
        # LibreChat parses name with: file.name.startsWith(path) where path = "session_id/fileId"
        assert item["name"] == "test-session-123/file-123", \
            f"name must be 'session_id/fileId' format, got: {item['name']}"
        # lastModified must be ISO 8601 with Z suffix for LibreChat's Date parsing
        assert item["lastModified"].endswith("Z"), \
            f"lastModified must end with 'Z', got: {item['lastModified']}"

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
        From crud.js: GET /download/{session_id}/{fileId} with responseType: 'arraybuffer'
        """
        self.mock_file_service.get_file_info.return_value = FileInfo(
            file_id="file-abc",
            filename="output.txt",
            size=17,
            content_type="text/plain",
            created_at=datetime.now(timezone.utc),
            path="/output.txt",
        )
        self.mock_file_service.get_file_content.return_value = b"file content here"

        response = client.get(
            "/download/test-session-789/file-abc", headers=auth_headers
        )

        assert response.status_code == 200
        assert response.content == b"file content here"
        assert "content-disposition" in response.headers


# =============================================================================
# LIBRECHAT AUTHENTICATION
# =============================================================================


class TestLibreChatAuthentication:
    """Test authentication exactly as LibreChat uses it.

    LibreChat only uses X-API-Key header for authentication.
    From CodeExecutor.ts: headers: { 'X-API-Key': apiKey }
    """

    @patch("src.services.orchestrator.ExecutionOrchestrator.execute")
    def test_x_api_key_header(self, mock_execute, client):
        """
        Test x-api-key header authentication on protected endpoint.

        LibreChat sends: headers: { 'X-API-Key': apiKey }
        """
        mock_execute.return_value = ExecResponse(
            session_id="auth-test", stdout="ok\n", stderr="", files=[]
        )
        headers = {"x-api-key": "test-api-key-for-testing-12345"}

        response = client.post(
            "/exec", json={"code": "print('ok')", "lang": "py"}, headers=headers
        )
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


# =============================================================================
# LIBRECHAT FILE LIFECYCLE
# =============================================================================


class TestLibreChatFileLifecycle:
    """Test the complete file lifecycle as LibreChat performs it.

    Full flow:
    1. Upload file via POST /upload (with 'file' singular field)
    2. Execute code referencing the uploaded file
    3. Check output files via GET /files/{session_id}?detail=summary
    4. Download output file via GET /download/{session_id}/{fileId}
    """

    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        """Set up mocks for full lifecycle tests."""
        self.mock_file_service = AsyncMock()
        self.mock_file_service.store_uploaded_file.return_value = "uploaded-file-001"
        self.mock_file_service.validate_uploads = MagicMock(return_value=None)

        self.mock_session_service = AsyncMock()
        self.mock_session_service.create_session.return_value = Session(
            session_id="lifecycle-session-123",
            status=SessionStatus.ACTIVE,
            created_at=datetime.now(timezone.utc),
            last_activity=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
            metadata={},
        )

        from src.dependencies.services import get_file_service, get_session_service

        app.dependency_overrides[get_file_service] = lambda: self.mock_file_service
        app.dependency_overrides[get_session_service] = lambda: self.mock_session_service

        yield

        app.dependency_overrides.clear()

    def test_upload_then_check_summary(self, client, auth_headers):
        """
        Test upload a file, then verify it appears in session file summary.

        This is the primeFiles check: upload -> GET /files/{session_id}?detail=summary
        """
        # Step 1: Upload file (LibreChat uses 'file' singular)
        upload_files = {"file": ("data.csv", io.BytesIO(b"col1,col2\n1,2\n"), "text/csv")}
        upload_data = {"entity_id": "asst_test_agent"}

        upload_response = client.post(
            "/upload", files=upload_files, data=upload_data, headers=auth_headers
        )
        assert upload_response.status_code == 200
        upload_result = upload_response.json()
        assert upload_result["message"] == "success"
        session_id = upload_result["session_id"]
        file_id = upload_result["files"][0]["fileId"]

        # Step 2: Check summary endpoint
        self.mock_file_service.list_files.return_value = [
            FileInfo(
                file_id=file_id,
                filename="data.csv",
                size=14,
                content_type="text/csv",
                created_at=datetime.now(timezone.utc),
                path="/data.csv",
            )
        ]

        summary_response = client.get(
            f"/files/{session_id}?detail=summary", headers=auth_headers
        )
        assert summary_response.status_code == 200
        summary_data = summary_response.json()
        assert isinstance(summary_data, list)
        assert len(summary_data) >= 1

        # Verify format matches what LibreChat's process.js parses
        item = summary_data[0]
        assert "name" in item
        assert "lastModified" in item
        # name must be in "session_id/fileId" format
        assert "/" in item["name"], "name must contain '/' separator"

    @patch("src.services.orchestrator.ExecutionOrchestrator.execute")
    def test_upload_then_exec_with_file_ref(self, mock_execute, client, auth_headers):
        """
        Test upload a file, then execute code that references it.

        LibreChat sends the session_id and fileId from upload response in exec request.
        """
        # Step 1: Upload
        upload_files = {"file": ("input.txt", io.BytesIO(b"hello world"), "text/plain")}
        upload_response = client.post(
            "/upload", files=upload_files, data={"entity_id": "asst_test"}, headers=auth_headers
        )
        assert upload_response.status_code == 200
        upload_result = upload_response.json()
        session_id = upload_result["session_id"]
        file_id = upload_result["files"][0]["fileId"]

        # Step 2: Execute with file reference
        mock_execute.return_value = ExecResponse(
            session_id=session_id,
            stdout="hello world\n",
            stderr="",
            files=[],
        )

        exec_response = client.post(
            "/exec",
            json={
                "code": "with open('/mnt/data/input.txt') as f: print(f.read())",
                "lang": "py",
                "files": [{"id": file_id, "session_id": session_id, "name": "input.txt"}],
            },
            headers=auth_headers,
        )
        assert exec_response.status_code == 200
        exec_data = exec_response.json()
        assert exec_data["session_id"] == session_id
        assert exec_data["stdout"] == "hello world\n"

    def test_download_output_file(self, client, auth_headers):
        """
        Test downloading an output file as LibreChat does.

        LibreChat calls: GET /download/{session_id}/{fileId} with responseType: 'arraybuffer'
        From crud.js: axios({ method: 'get', url, responseType: 'arraybuffer' })
        """
        session_id = "lifecycle-session-123"
        file_id = "output-file-456"
        file_content = b"\x89PNG\r\n\x1a\n fake image content"

        self.mock_file_service.get_file_info.return_value = FileInfo(
            file_id=file_id,
            filename="chart.png",
            size=len(file_content),
            content_type="image/png",
            created_at=datetime.now(timezone.utc),
            path="/chart.png",
        )
        self.mock_file_service.get_file_content.return_value = file_content

        response = client.get(
            f"/download/{session_id}/{file_id}", headers=auth_headers
        )

        assert response.status_code == 200
        assert response.content == file_content
        assert "content-disposition" in response.headers

    def test_librechat_user_agent_header(self, client, auth_headers):
        """
        Test that User-Agent: LibreChat/1.0 header works correctly.

        LibreChat always sends this header. Verify it doesn't cause issues.
        """
        headers = {
            **auth_headers,
            "User-Agent": "LibreChat/1.0",
            "User-Id": "user_abc123",
        }
        upload_files = {"file": ("test.txt", io.BytesIO(b"test"), "text/plain")}

        response = client.post("/upload", files=upload_files, headers=headers)
        assert response.status_code == 200


# =============================================================================
# LIBRECHAT PRIME FILES FLOW
# =============================================================================


class TestLibreChatPrimeFiles:
    """Test the primeFiles() flow from LibreChat's process.js.

    primeFiles() checks if previously uploaded files still exist in the
    code interpreter session, and re-uploads them if they've expired.

    Flow:
    1. GET /files/{session_id}?detail=summary
    2. Check response for file by matching name.startsWith("session_id/fileId")
    3. Check if lastModified is less than 23 hours old
    4. If missing or expired, re-upload via POST /upload
    """

    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        """Set up mocks for primeFiles tests."""
        self.mock_file_service = AsyncMock()
        self.mock_file_service.validate_uploads = MagicMock(return_value=None)
        self.mock_file_service.store_uploaded_file.return_value = "reuploaded-file-001"

        self.mock_session_service = AsyncMock()
        self.mock_session_service.create_session.return_value = Session(
            session_id="prime-session-123",
            status=SessionStatus.ACTIVE,
            created_at=datetime.now(timezone.utc),
            last_activity=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
            metadata={},
        )

        from src.dependencies.services import get_file_service, get_session_service

        app.dependency_overrides[get_file_service] = lambda: self.mock_file_service
        app.dependency_overrides[get_session_service] = lambda: self.mock_session_service

        yield

        app.dependency_overrides.clear()

    def test_prime_files_check_existing(self, client, auth_headers):
        """
        Test checking if a file exists via summary endpoint.

        LibreChat calls: GET /files/{session_id}?detail=summary
        Then checks: response.data.find(file => file.name.startsWith(path))
        """
        session_id = "prime-session-123"
        file_id = "prime-file-456"

        self.mock_file_service.list_files.return_value = [
            FileInfo(
                file_id=file_id,
                filename="data.csv",
                size=100,
                content_type="text/csv",
                created_at=datetime.now(timezone.utc),
                path="/data.csv",
            )
        ]

        response = client.get(
            f"/files/{session_id}?detail=summary", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1

        # Simulate LibreChat's client-side parsing:
        # file.name.startsWith("session_id/fileId")
        file_identifier = f"{session_id}/{file_id}"
        matching = [f for f in data if f["name"].startswith(file_identifier)]
        assert len(matching) == 1, \
            f"LibreChat expects to find file by name.startsWith('{file_identifier}')"

    def test_prime_files_reupload_flow(self, client, auth_headers):
        """
        Test the re-upload flow when file is expired.

        After checking summary, LibreChat re-uploads via POST /upload
        if the file is missing or expired (>23 hours old).
        """
        session_id = "expired-session-123"

        # Step 1: Summary returns empty (file expired/cleaned up)
        self.mock_file_service.list_files.return_value = []

        response = client.get(
            f"/files/{session_id}?detail=summary", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data == [], "Empty session should return empty array"

        # Step 2: Re-upload the file (LibreChat uses 'file' singular)
        upload_files = {"file": ("data.csv", io.BytesIO(b"col1,col2\n"), "text/csv")}
        upload_data = {"entity_id": "asst_reupload_test"}

        upload_response = client.post(
            "/upload", files=upload_files, data=upload_data, headers=auth_headers
        )
        assert upload_response.status_code == 200
        result = upload_response.json()
        assert result["message"] == "success"
        assert "session_id" in result
        assert len(result["files"]) == 1

    def test_prime_files_empty_session_returns_empty_array(self, client, auth_headers):
        """
        Test that non-existent session returns empty array, not 404.

        LibreChat expects an empty array for sessions with no files.
        A 404 would cause an error in primeFiles().
        """
        self.mock_file_service.list_files.return_value = []

        response = client.get(
            "/files/nonexistent-session-xyz?detail=summary", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data == [], "Non-existent session must return [], not 404"

    def test_prime_files_name_format_matches_client_parsing(self, client, auth_headers):
        """
        Test that the name field format can be parsed by LibreChat.

        LibreChat splits the fileIdentifier as:
          const [path, queryString] = fileIdentifier.split('?')
          const [session_id, id] = path.split('/')

        So the name in summary must be "session_id/fileId" format.
        """
        session_id = "parse-test-session"
        file_id = "parse-test-file"

        self.mock_file_service.list_files.return_value = [
            FileInfo(
                file_id=file_id,
                filename="result.json",
                size=50,
                content_type="application/json",
                created_at=datetime.now(timezone.utc),
                path="/result.json",
            )
        ]

        response = client.get(
            f"/files/{session_id}?detail=summary", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1

        name = data[0]["name"]
        # Simulate LibreChat's parsing
        parts = name.split("/")
        assert len(parts) == 2, f"name must have exactly 2 parts split by '/', got: {name}"
        parsed_session_id, parsed_file_id = parts
        assert parsed_session_id == session_id, \
            f"First part must be session_id '{session_id}', got: '{parsed_session_id}'"
        assert parsed_file_id == file_id, \
            f"Second part must be file_id '{file_id}', got: '{parsed_file_id}'"

    def test_prime_files_last_modified_is_parseable_date(self, client, auth_headers):
        """
        Test that lastModified is a parseable date string.

        LibreChat uses: checkIfActive(dateString) which creates new Date(dateString).
        The date must be valid JavaScript Date-parseable ISO 8601 format.
        """
        self.mock_file_service.list_files.return_value = [
            FileInfo(
                file_id="date-test-file",
                filename="test.txt",
                size=10,
                content_type="text/plain",
                created_at=datetime.now(timezone.utc),
                path="/test.txt",
            )
        ]

        response = client.get(
            "/files/date-test-session?detail=summary", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        last_modified = data[0]["lastModified"]

        # Must be parseable as ISO 8601 datetime
        parsed = datetime.fromisoformat(last_modified.replace("Z", "+00:00"))
        assert parsed is not None, "lastModified must be parseable ISO 8601"
        # Must end with Z (UTC) for JavaScript Date compatibility
        assert last_modified.endswith("Z"), \
            f"lastModified must end with 'Z' for JS Date parsing, got: {last_modified}"


# =============================================================================
# LIBRECHAT CONCURRENCY AND HEADERS
# =============================================================================


class TestLibreChatConcurrency:
    """Test rapid sequential access patterns that LibreChat may produce.

    LibreChat can fire multiple tool calls in parallel, leading to
    multiple exec requests that reference the same session or files.
    TestClient is not thread-safe, so we test rapid sequential requests.
    """

    @patch("src.services.orchestrator.ExecutionOrchestrator.execute")
    def test_rapid_exec_requests(self, mock_execute, client, auth_headers):
        """
        Test multiple rapid exec requests can be processed without errors.

        LibreChat may send parallel tool calls that execute code simultaneously.
        Each should get a valid response.
        """
        mock_execute.return_value = ExecResponse(
            session_id="concurrent-session", stdout="ok\n", stderr="", files=[]
        )

        responses = []
        for i in range(5):
            resp = client.post(
                "/exec",
                json={"code": f"print({i})", "lang": "py"},
                headers=auth_headers,
            )
            responses.append(resp)

        # All requests should succeed
        for resp in responses:
            assert resp.status_code == 200
            data = resp.json()
            assert "session_id" in data
            assert "stdout" in data
            assert "stderr" in data

    @patch("src.services.orchestrator.ExecutionOrchestrator.execute")
    def test_rapid_exec_same_session(self, mock_execute, client, auth_headers):
        """
        Test rapid exec requests referencing the same session_id.

        LibreChat may have multiple requests accessing the same session.
        """
        session_id = "shared-session-123"
        mock_execute.return_value = ExecResponse(
            session_id=session_id, stdout="result\n", stderr="", files=[]
        )

        responses = []
        for i in range(3):
            resp = client.post(
                "/exec",
                json={
                    "code": f"x = {i}",
                    "lang": "py",
                    "session_id": session_id,
                },
                headers=auth_headers,
            )
            responses.append(resp)

        for resp in responses:
            assert resp.status_code == 200


class TestLibreChatFullHeaders:
    """Test that the full set of headers LibreChat sends work correctly.

    LibreChat sends various headers depending on the operation.
    These tests verify none of them cause issues.
    """

    @patch("src.services.orchestrator.ExecutionOrchestrator.execute")
    def test_exec_with_full_librechat_headers(self, mock_execute, client):
        """
        Test exec request with all headers LibreChat sends.

        From CodeExecutor.ts and crud.js, LibreChat sends:
        - X-API-Key: apiKey
        - User-Agent: LibreChat/1.0
        - Content-Type: application/json
        """
        mock_execute.return_value = ExecResponse(
            session_id="header-test", stdout="ok\n", stderr="", files=[]
        )

        headers = {
            "X-API-Key": "test-api-key-for-testing-12345",
            "User-Agent": "LibreChat/1.0",
            "Content-Type": "application/json",
        }

        response = client.post(
            "/exec",
            json={"code": "print('ok')", "lang": "py"},
            headers=headers,
        )
        assert response.status_code == 200

    def test_upload_with_full_librechat_headers(self, client):
        """
        Test upload request with all headers LibreChat sends.

        From crud.js, LibreChat sends:
        - X-API-Key: apiKey
        - User-Agent: LibreChat/1.0
        - User-Id: req.user.id
        - Content-Type: multipart/form-data (set by form)
        """
        mock_file_service = AsyncMock()
        mock_file_service.store_uploaded_file.return_value = "header-test-file"
        mock_file_service.validate_uploads = MagicMock(return_value=None)

        mock_session_service = AsyncMock()
        mock_session_service.create_session.return_value = Session(
            session_id="header-test-session",
            status=SessionStatus.ACTIVE,
            created_at=datetime.now(timezone.utc),
            last_activity=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
            metadata={},
        )

        from src.dependencies.services import get_file_service, get_session_service

        app.dependency_overrides[get_file_service] = lambda: mock_file_service
        app.dependency_overrides[get_session_service] = lambda: mock_session_service

        try:
            headers = {
                "X-API-Key": "test-api-key-for-testing-12345",
                "User-Agent": "LibreChat/1.0",
                "User-Id": "user_header_test",
            }

            upload_files = {"file": ("test.txt", io.BytesIO(b"content"), "text/plain")}
            upload_data = {"entity_id": "asst_header_test"}

            response = client.post(
                "/upload", files=upload_files, data=upload_data, headers=headers
            )
            assert response.status_code == 200
            result = response.json()
            assert result["message"] == "success"
        finally:
            app.dependency_overrides.clear()

    def test_download_with_full_librechat_headers(self, client):
        """
        Test download request with LibreChat headers.

        From crud.js: headers: { 'User-Agent': 'LibreChat/1.0', 'X-API-Key': apiKey }
        Timeout: 15000ms
        """
        mock_file_service = AsyncMock()
        mock_file_service.get_file_info.return_value = FileInfo(
            file_id="dl-file",
            filename="output.txt",
            size=5,
            content_type="text/plain",
            created_at=datetime.now(timezone.utc),
            path="/output.txt",
        )
        mock_file_service.get_file_content.return_value = b"hello"

        from src.dependencies.services import get_file_service

        app.dependency_overrides[get_file_service] = lambda: mock_file_service

        try:
            headers = {
                "X-API-Key": "test-api-key-for-testing-12345",
                "User-Agent": "LibreChat/1.0",
            }

            response = client.get(
                "/download/dl-session/dl-file", headers=headers
            )
            assert response.status_code == 200
            assert response.content == b"hello"
        finally:
            app.dependency_overrides.clear()
