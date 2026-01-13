"""Integration tests for the /exec endpoint."""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch, MagicMock
import json
import time
from datetime import datetime, timezone, timedelta

from src.main import app
from src.models import CodeExecution, ExecutionStatus, ExecutionOutput, OutputType


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Provide authentication headers for tests."""
    return {"x-api-key": "test-api-key-for-testing-12345"}


@pytest.fixture
def mock_session_service():
    """Mock session service for testing."""
    service = AsyncMock()

    # Mock session creation
    from src.models.session import Session, SessionStatus
    from datetime import datetime, timezone, timedelta

    mock_session = Session(
        session_id="test-session-123",
        status=SessionStatus.ACTIVE,
        created_at=datetime.now(timezone.utc),
        last_activity=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        metadata={"entity_id": "test-entity"},
    )

    service.create_session.return_value = mock_session
    service.get_session.return_value = mock_session
    service.validate_session_access.return_value = True

    return service


@pytest.fixture
def mock_execution_service():
    """Mock execution service for testing."""
    service = AsyncMock()

    # Mock successful execution
    mock_execution = CodeExecution(
        execution_id="exec-123",
        session_id="test-session-123",
        code="print('Hello, World!')",
        language="py",
        status=ExecutionStatus.COMPLETED,
        exit_code=0,
        execution_time_ms=150,
        outputs=[
            ExecutionOutput(
                type=OutputType.STDOUT,
                content="Hello, World!",
                timestamp=datetime.now(timezone.utc),
            )
        ],
    )

    service.execute_code.return_value = (mock_execution, None, None, [], "pool_hit")

    return service


@pytest.fixture
def mock_file_service():
    """Mock file service for testing."""
    service = AsyncMock()
    service.list_files.return_value = []
    return service


@pytest.fixture(autouse=True)
def mock_dependencies(mock_session_service, mock_execution_service, mock_file_service):
    """Mock all dependencies for testing."""
    from src.dependencies.services import (
        get_session_service,
        get_execution_service,
        get_file_service,
    )

    # Override the dependencies in the FastAPI app
    app.dependency_overrides[get_session_service] = lambda: mock_session_service
    app.dependency_overrides[get_execution_service] = lambda: mock_execution_service
    app.dependency_overrides[get_file_service] = lambda: mock_file_service

    yield

    # Clean up after test
    app.dependency_overrides.clear()


class TestExecEndpoint:
    """Test the /exec endpoint functionality."""

    def test_exec_simple_python_code(
        self, client, auth_headers, mock_execution_service
    ):
        """Test executing simple Python code."""
        request_data = {"code": "print('Hello, World!')", "lang": "py"}

        response = client.post("/exec", json=request_data, headers=auth_headers)

        assert response.status_code == 200
        response_data = response.json()

        # Check LibreChat-compatible response structure
        assert "session_id" in response_data
        assert "files" in response_data
        assert "stdout" in response_data
        assert "stderr" in response_data

        # Check stdout content (should end with newline for LibreChat compatibility)
        assert response_data["stdout"] == "Hello, World!\n"

        # Check files array
        assert isinstance(response_data["files"], list)

        # Verify service was called
        mock_execution_service.execute_code.assert_called_once()

    def test_exec_with_entity_id(
        self, client, auth_headers, mock_session_service, mock_execution_service
    ):
        """Test executing code with entity_id for session sharing."""
        request_data = {
            "code": "print('Hello from entity!')",
            "lang": "py",
            "entity_id": "test-entity-123",
        }

        response = client.post("/exec", json=request_data, headers=auth_headers)

        assert response.status_code == 200
        response_data = response.json()

        # Should create session with entity metadata
        mock_session_service.create_session.assert_called_once()
        create_call = mock_session_service.create_session.call_args[0][0]
        assert create_call.metadata["entity_id"] == "test-entity-123"

    @pytest.mark.skip(
        reason="Mock file service returns AsyncMock instead of proper values"
    )
    def test_exec_with_files(self, client, auth_headers, mock_execution_service):
        """Test executing code with file references."""
        request_data = {
            "code": "with open('data.txt', 'r') as f: print(f.read())",
            "lang": "py",
            "files": [
                {"id": "file-123", "session_id": "test-session", "name": "data.txt"}
            ],
        }

        response = client.post("/exec", json=request_data, headers=auth_headers)

        assert response.status_code == 200

        # Verify execution was called with files
        mock_execution_service.execute_code.assert_called_once()
        call_args = mock_execution_service.execute_code.call_args
        files_arg = call_args[1]["files"]  # keyword argument
        assert len(files_arg) == 1
        assert files_arg[0]["id"] == "file-123"

    def test_exec_different_languages(
        self, client, auth_headers, mock_execution_service
    ):
        """Test executing code in different languages."""
        test_cases = [
            {"lang": "py", "code": "print('Hello Python')"},
            {"lang": "js", "code": "console.log('Hello JavaScript')"},
            {
                "lang": "go",
                "code": 'package main\nimport "fmt"\nfunc main() { fmt.Println("Hello Go") }',
            },
            {
                "lang": "java",
                "code": 'public class Main { public static void main(String[] args) { System.out.println("Hello Java"); } }',
            },
        ]

        for test_case in test_cases:
            response = client.post("/exec", json=test_case, headers=auth_headers)
            assert response.status_code == 200

            response_data = response.json()
            # Language is no longer returned in the response for LibreChat compatibility
            assert "session_id" in response_data

    def test_exec_with_execution_error(
        self, client, auth_headers, mock_execution_service
    ):
        """Test handling execution errors."""
        # Mock failed execution
        failed_execution = CodeExecution(
            execution_id="exec-failed",
            session_id="test-session-123",
            code="print(undefined_variable)",
            language="py",
            status=ExecutionStatus.FAILED,
            exit_code=1,
            error_message="NameError: name 'undefined_variable' is not defined",
            outputs=[
                ExecutionOutput(
                    type=OutputType.STDERR,
                    content="NameError: name 'undefined_variable' is not defined",
                    timestamp=datetime.now(timezone.utc),
                )
            ],
        )

        mock_execution_service.execute_code.return_value = (
            failed_execution,
            None,
            None,
            [],
            "pool_hit",
        )

        request_data = {"code": "print(undefined_variable)", "lang": "py"}

        response = client.post("/exec", json=request_data, headers=auth_headers)

        assert response.status_code == 200  # Still 200, but with error in response
        response_data = response.json()

        # For failed executions, content may be empty or contain error info
        # In LibreChat format, errors would typically be in stderr which isn't directly exposed
        # but the test shows the execution completed and returned a response

    def test_exec_with_timeout(self, client, auth_headers, mock_execution_service):
        """Test handling execution timeout."""
        # Mock timeout execution
        timeout_execution = CodeExecution(
            execution_id="exec-timeout",
            session_id="test-session-123",
            code="import time; time.sleep(100)",
            language="py",
            status=ExecutionStatus.TIMEOUT,
            error_message="Execution timed out after 30 seconds",
        )

        mock_execution_service.execute_code.return_value = (
            timeout_execution,
            None,
            None,
            [],
            "pool_hit",
        )

        request_data = {"code": "import time; time.sleep(100)", "lang": "py"}

        response = client.post("/exec", json=request_data, headers=auth_headers)

        assert response.status_code == 200
        response_data = response.json()

        # For timeout, we expect LibreChat format but stdout may be empty or contain timeout message
        assert "session_id" in response_data
        assert "files" in response_data
        assert "stdout" in response_data
        assert "stderr" in response_data

    def test_exec_invalid_language(self, client, auth_headers):
        """Test executing code with invalid language."""
        request_data = {"code": "print('Hello')", "lang": "invalid_language"}

        response = client.post("/exec", json=request_data, headers=auth_headers)

        # Should either return error or handle gracefully
        assert response.status_code in [200, 400, 422]

    def test_exec_empty_code(self, client, auth_headers):
        """Test executing empty code."""
        request_data = {"code": "", "lang": "py"}

        response = client.post("/exec", json=request_data, headers=auth_headers)

        # Should return validation error (400 for business logic validation)
        assert response.status_code == 400

    def test_exec_missing_required_fields(self, client, auth_headers):
        """Test request with missing required fields."""
        # Missing code
        response = client.post("/exec", json={"lang": "py"}, headers=auth_headers)
        assert response.status_code == 422

        # Missing lang
        response = client.post(
            "/exec", json={"code": "print('test')"}, headers=auth_headers
        )
        assert response.status_code == 422

    def test_exec_with_args(self, client, auth_headers, mock_execution_service):
        """Test executing code with command line arguments."""
        request_data = {
            "code": "import sys; print(' '.join(sys.argv[1:]))",
            "lang": "py",
            "args": "arg1 arg2 arg3",
        }

        response = client.post("/exec", json=request_data, headers=auth_headers)

        assert response.status_code == 200
        # Args handling would be implementation-specific

    def test_exec_with_user_id(self, client, auth_headers, mock_execution_service):
        """Test executing code with user_id for tracking."""
        request_data = {
            "code": "print('Hello User')",
            "lang": "py",
            "user_id": "user-123",
        }

        response = client.post("/exec", json=request_data, headers=auth_headers)

        assert response.status_code == 200
        # User ID would be used for logging/tracking

    def test_exec_session_reuse(
        self, client, auth_headers, mock_session_service, mock_execution_service
    ):
        """Test that sessions are reused for the same entity."""
        request_data = {"code": "x = 1", "lang": "py", "entity_id": "test-entity"}

        # First execution
        response1 = client.post("/exec", json=request_data, headers=auth_headers)
        assert response1.status_code == 200
        session_id_1 = response1.json()["session_id"]

        # Second execution with same entity
        request_data["code"] = "print(x)"
        response2 = client.post("/exec", json=request_data, headers=auth_headers)
        assert response2.status_code == 200
        session_id_2 = response2.json()["session_id"]

        # Should reuse the same session
        assert session_id_1 == session_id_2

    def test_exec_without_authentication(self, client):
        """Test executing code without authentication."""
        request_data = {"code": "print('Hello')", "lang": "py"}

        response = client.post("/exec", json=request_data)

        assert response.status_code == 401

    def test_exec_with_invalid_api_key(self, client):
        """Test executing code with invalid API key."""
        request_data = {"code": "print('Hello')", "lang": "py"}

        headers = {"x-api-key": "invalid-key"}
        response = client.post("/exec", json=request_data, headers=headers)

        assert response.status_code == 401

    def test_exec_service_error(self, client, auth_headers, mock_execution_service):
        """Test handling service errors during execution."""
        mock_execution_service.execute_code.side_effect = Exception("Service error")

        request_data = {"code": "print('Hello')", "lang": "py"}

        response = client.post("/exec", json=request_data, headers=auth_headers)

        # 503 Service Unavailable for backend service errors
        assert response.status_code == 503
        assert "error" in response.json()

    def test_exec_response_format_compatibility(
        self, client, auth_headers, mock_execution_service
    ):
        """Test that response format is compatible with LibreChat API."""
        request_data = {"code": "print('Hello, World!')", "lang": "py"}

        response = client.post("/exec", json=request_data, headers=auth_headers)

        assert response.status_code == 200
        response_data = response.json()

        # Check LibreChat-compatible structure
        required_fields = ["session_id", "files", "stdout", "stderr"]
        for field in required_fields:
            assert field in response_data

        # Check that files is a list
        assert isinstance(response_data["files"], list)

    @pytest.mark.skip(
        reason="Mock file service returns AsyncMock instead of proper values"
    )
    def test_exec_with_generated_files(
        self, client, auth_headers, mock_execution_service, mock_file_service
    ):
        """Test execution that generates files."""
        # Mock execution with file output
        execution_with_files = CodeExecution(
            execution_id="exec-with-files",
            session_id="test-session-123",
            code="with open('output.txt', 'w') as f: f.write('Generated content')",
            language="py",
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            outputs=[
                ExecutionOutput(
                    type=OutputType.FILE,
                    content="/workspace/output.txt",
                    mime_type="text/plain",
                    size=17,
                    timestamp=datetime.now(timezone.utc),
                )
            ],
        )

        mock_execution_service.execute_code.return_value = (
            execution_with_files,
            None,
            None,
            [],
            "pool_hit",
        )

        # Mock file service to return generated file
        from src.models.files import FileInfo

        mock_file_info = FileInfo(
            file_id="generated-file-123",
            filename="output.txt",
            size=17,
            content_type="text/plain",
            created_at=datetime.now(timezone.utc),
            path="/output.txt",
        )
        mock_file_service.list_files.return_value = [mock_file_info]

        request_data = {
            "code": "with open('output.txt', 'w') as f: f.write('Generated content')",
            "lang": "py",
        }

        response = client.post("/exec", json=request_data, headers=auth_headers)

        assert response.status_code == 200
        response_data = response.json()

        # Should include generated files in files array
        assert len(response_data["files"]) == 1
        assert response_data["files"][0]["id"] == "generated-file-123"
        assert response_data["files"][0]["name"] == "output.txt"

    def test_exec_large_output_handling(
        self, client, auth_headers, mock_execution_service
    ):
        """Test handling of large execution output."""
        # Mock execution with large output
        large_output = "A" * 100000  # 100KB output

        large_execution = CodeExecution(
            execution_id="exec-large",
            session_id="test-session-123",
            code="print('A' * 100000)",
            language="py",
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            outputs=[
                ExecutionOutput(
                    type=OutputType.STDOUT,
                    content=large_output,
                    timestamp=datetime.now(timezone.utc),
                )
            ],
        )

        mock_execution_service.execute_code.return_value = (
            large_execution,
            None,
            None,
            [],
            "pool_hit",
        )

        request_data = {"code": "print('A' * 100000)", "lang": "py"}

        response = client.post("/exec", json=request_data, headers=auth_headers)

        assert response.status_code == 200
        response_data = response.json()

        # Output should be present in stdout field (may be truncated)
        assert "stdout" in response_data
        assert len(response_data["stdout"]) > 0
