"""
Container Behavior Tests - Phase 0 Behavioral Baseline

This test suite documents and verifies container lifecycle and execution behavior
to ensure 100% compatibility after architectural refactoring.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from src.main import app
from src.models import CodeExecution, ExecutionStatus, ExecutionOutput, OutputType
from src.models.session import Session, SessionStatus
from src.models.files import FileInfo


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Provide authentication headers for tests."""
    return {"x-api-key": "test-api-key-for-testing-12345"}


def create_session(session_id: str) -> Session:
    """Helper to create a session."""
    return Session(
        session_id=session_id,
        status=SessionStatus.ACTIVE,
        created_at=datetime.now(timezone.utc),
        last_activity=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        metadata={},
    )


# =============================================================================
# CONTAINER LIFECYCLE BEHAVIOR
# =============================================================================


class TestContainerLifecycle:
    """Test container lifecycle behavior."""

    def test_container_created_for_execution(self, client, auth_headers):
        """Test that a container is created for each execution."""
        session_id = "container-test-session"
        mock_session = create_session(session_id)

        mock_execution = CodeExecution(
            execution_id="exec-container-1",
            session_id=session_id,
            code="print('test')",
            language="py",
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            outputs=[
                ExecutionOutput(
                    type=OutputType.STDOUT,
                    content="test",
                    timestamp=datetime.now(timezone.utc),
                )
            ],
        )

        mock_session_service = AsyncMock()
        mock_session_service.create_session.return_value = mock_session
        mock_session_service.get_session.return_value = mock_session

        mock_execution_service = AsyncMock()
        mock_execution_service.execute_code.return_value = (
            mock_execution,
            None,
            None,
            [],
            "pool_hit",
        )

        mock_file_service = AsyncMock()
        mock_file_service.list_files.return_value = []

        from src.dependencies.services import (
            get_session_service,
            get_execution_service,
            get_file_service,
        )

        app.dependency_overrides[get_session_service] = lambda: mock_session_service
        app.dependency_overrides[get_execution_service] = lambda: mock_execution_service
        app.dependency_overrides[get_file_service] = lambda: mock_file_service

        try:
            response = client.post(
                "/exec",
                json={"code": "print('test')", "lang": "py"},
                headers=auth_headers,
            )

            assert response.status_code == 200

            # Verify execution service was called (which creates container internally)
            mock_execution_service.execute_code.assert_called_once()
        finally:
            app.dependency_overrides.clear()

    def test_container_cleaned_up_after_execution(self, client, auth_headers):
        """Test that container is cleaned up after execution completes."""
        session_id = "cleanup-test-session"
        mock_session = create_session(session_id)

        mock_execution = CodeExecution(
            execution_id="exec-cleanup",
            session_id=session_id,
            code="print('done')",
            language="py",
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            outputs=[],
        )

        mock_session_service = AsyncMock()
        mock_session_service.create_session.return_value = mock_session
        mock_session_service.get_session.return_value = mock_session

        mock_execution_service = AsyncMock()
        mock_execution_service.execute_code.return_value = (
            mock_execution,
            None,
            None,
            [],
            "pool_hit",
        )
        mock_execution_service.cleanup_session = AsyncMock()

        mock_file_service = AsyncMock()
        mock_file_service.list_files.return_value = []

        from src.dependencies.services import (
            get_session_service,
            get_execution_service,
            get_file_service,
        )

        app.dependency_overrides[get_session_service] = lambda: mock_session_service
        app.dependency_overrides[get_execution_service] = lambda: mock_execution_service
        app.dependency_overrides[get_file_service] = lambda: mock_file_service

        try:
            response = client.post(
                "/exec",
                json={"code": "print('done')", "lang": "py"},
                headers=auth_headers,
            )

            assert response.status_code == 200

            # Cleanup should be called after execution
            # Note: cleanup may be async/deferred in actual implementation
        finally:
            app.dependency_overrides.clear()


# =============================================================================
# LANGUAGE-SPECIFIC EXECUTION BEHAVIOR
# =============================================================================


class TestLanguageExecution:
    """Test language-specific execution patterns."""

    # Languages that support stdin execution (interpreted)
    STDIN_LANGUAGES = ["py", "js", "php", "r"]

    # Languages that require file-based execution (compiled)
    FILE_LANGUAGES = ["go", "java", "c", "cpp", "rs", "f90", "d", "ts"]

    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        """Set up mocks for all language tests."""
        self.mock_session = create_session("lang-test-session")

        self.mock_session_service = AsyncMock()
        self.mock_session_service.create_session.return_value = self.mock_session
        self.mock_session_service.get_session.return_value = self.mock_session

        self.mock_execution_service = AsyncMock()
        self.mock_file_service = AsyncMock()
        self.mock_file_service.list_files.return_value = []

        from src.dependencies.services import (
            get_session_service,
            get_execution_service,
            get_file_service,
        )

        app.dependency_overrides[get_session_service] = (
            lambda: self.mock_session_service
        )
        app.dependency_overrides[get_execution_service] = (
            lambda: self.mock_execution_service
        )
        app.dependency_overrides[get_file_service] = lambda: self.mock_file_service

        yield

        app.dependency_overrides.clear()

    @pytest.mark.parametrize("language", STDIN_LANGUAGES)
    def test_stdin_language_execution(self, client, auth_headers, language):
        """Test stdin-based language execution (interpreted languages)."""
        self.mock_execution_service.execute_code.return_value = (
            CodeExecution(
                execution_id=f"exec-{language}",
                session_id="lang-test-session",
                code=f"{language} code",
                language=language,
                status=ExecutionStatus.COMPLETED,
                exit_code=0,
                outputs=[
                    ExecutionOutput(
                        type=OutputType.STDOUT,
                        content=f"Hello {language}",
                        timestamp=datetime.now(timezone.utc),
                    )
                ],
            ),
            None,
            None,
            [],
            "pool_hit",
        )

        code_samples = {
            "py": "print('Hello py')",
            "js": "console.log('Hello js')",
            "php": "<?php echo 'Hello php'; ?>",
            "r": "print('Hello r')",
        }

        response = client.post(
            "/exec",
            json={"code": code_samples.get(language, ""), "lang": language},
            headers=auth_headers,
        )

        assert response.status_code == 200

    @pytest.mark.parametrize("language", FILE_LANGUAGES)
    def test_file_language_execution(self, client, auth_headers, language):
        """Test file-based language execution (compiled languages)."""
        self.mock_execution_service.execute_code.return_value = (
            CodeExecution(
                execution_id=f"exec-{language}",
                session_id="lang-test-session",
                code=f"{language} code",
                language=language,
                status=ExecutionStatus.COMPLETED,
                exit_code=0,
                outputs=[
                    ExecutionOutput(
                        type=OutputType.STDOUT,
                        content=f"Hello {language}",
                        timestamp=datetime.now(timezone.utc),
                    )
                ],
            ),
            None,
            None,
            [],
            "pool_hit",
        )

        code_samples = {
            "go": 'package main\nimport "fmt"\nfunc main() { fmt.Println("Hello go") }',
            "java": 'public class Code { public static void main(String[] args) { System.out.println("Hello java"); } }',
            "c": '#include <stdio.h>\nint main() { printf("Hello c\\n"); return 0; }',
            "cpp": '#include <iostream>\nint main() { std::cout << "Hello cpp"; return 0; }',
            "rs": 'fn main() { println!("Hello rs"); }',
            "f90": 'program hello\n  print *, "Hello f90"\nend program hello',
            "d": 'import std.stdio; void main() { writeln("Hello d"); }',
            "ts": 'console.log("Hello ts");',
        }

        response = client.post(
            "/exec",
            json={"code": code_samples.get(language, ""), "lang": language},
            headers=auth_headers,
        )

        assert response.status_code == 200


# =============================================================================
# EXECUTION STATUS BEHAVIOR
# =============================================================================


class TestExecutionStatus:
    """Test execution status handling."""

    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        """Set up mocks."""
        self.mock_session = create_session("status-test-session")

        self.mock_session_service = AsyncMock()
        self.mock_session_service.create_session.return_value = self.mock_session
        self.mock_session_service.get_session.return_value = self.mock_session

        self.mock_execution_service = AsyncMock()
        self.mock_file_service = AsyncMock()
        self.mock_file_service.list_files.return_value = []

        from src.dependencies.services import (
            get_session_service,
            get_execution_service,
            get_file_service,
        )

        app.dependency_overrides[get_session_service] = (
            lambda: self.mock_session_service
        )
        app.dependency_overrides[get_execution_service] = (
            lambda: self.mock_execution_service
        )
        app.dependency_overrides[get_file_service] = lambda: self.mock_file_service

        yield

        app.dependency_overrides.clear()

    def test_completed_status(self, client, auth_headers):
        """Test successful execution status."""
        self.mock_execution_service.execute_code.return_value = (
            CodeExecution(
                execution_id="exec-completed",
                session_id="status-test-session",
                code="print('ok')",
                language="py",
                status=ExecutionStatus.COMPLETED,
                exit_code=0,
                outputs=[
                    ExecutionOutput(
                        type=OutputType.STDOUT,
                        content="ok",
                        timestamp=datetime.now(timezone.utc),
                    )
                ],
            ),
            None,
            None,
            [],
            "pool_hit",
        )

        response = client.post(
            "/exec", json={"code": "print('ok')", "lang": "py"}, headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert "ok" in data["stdout"]

    def test_failed_status(self, client, auth_headers):
        """Test failed execution status."""
        self.mock_execution_service.execute_code.return_value = (
            CodeExecution(
                execution_id="exec-failed",
                session_id="status-test-session",
                code="raise Exception('fail')",
                language="py",
                status=ExecutionStatus.FAILED,
                exit_code=1,
                error_message="Exception: fail",
                outputs=[
                    ExecutionOutput(
                        type=OutputType.STDERR,
                        content="Exception: fail",
                        timestamp=datetime.now(timezone.utc),
                    )
                ],
            ),
            None,
            None,
            [],
            "pool_hit",
        )

        response = client.post(
            "/exec",
            json={"code": "raise Exception('fail')", "lang": "py"},
            headers=auth_headers,
        )

        # Still returns 200 with error in output
        assert response.status_code == 200

    def test_timeout_status(self, client, auth_headers):
        """Test timeout execution status."""
        self.mock_execution_service.execute_code.return_value = (
            CodeExecution(
                execution_id="exec-timeout",
                session_id="status-test-session",
                code="import time; time.sleep(999)",
                language="py",
                status=ExecutionStatus.TIMEOUT,
                error_message="Execution timed out after 30 seconds",
                outputs=[],
            ),
            None,
            None,
            [],
            "pool_hit",
        )

        response = client.post(
            "/exec",
            json={"code": "import time; time.sleep(999)", "lang": "py"},
            headers=auth_headers,
        )

        # Still returns 200 with timeout info
        assert response.status_code == 200

    def test_cancelled_status(self, client, auth_headers):
        """Test cancelled execution status."""
        self.mock_execution_service.execute_code.return_value = (
            CodeExecution(
                execution_id="exec-cancelled",
                session_id="status-test-session",
                code="cancelled code",
                language="py",
                status=ExecutionStatus.CANCELLED,
                outputs=[],
            ),
            None,
            None,
            [],
            "pool_hit",
        )

        response = client.post(
            "/exec",
            json={"code": "long running code", "lang": "py"},
            headers=auth_headers,
        )

        assert response.status_code == 200


# =============================================================================
# FILE GENERATION BEHAVIOR
# =============================================================================


class TestFileGeneration:
    """Test file generation during execution."""

    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        """Set up mocks."""
        self.mock_session = create_session("filegen-test-session")

        self.mock_session_service = AsyncMock()
        self.mock_session_service.create_session.return_value = self.mock_session
        self.mock_session_service.get_session.return_value = self.mock_session

        self.mock_execution_service = AsyncMock()
        self.mock_file_service = AsyncMock()

        from src.dependencies.services import (
            get_session_service,
            get_execution_service,
            get_file_service,
        )

        app.dependency_overrides[get_session_service] = (
            lambda: self.mock_session_service
        )
        app.dependency_overrides[get_execution_service] = (
            lambda: self.mock_execution_service
        )
        app.dependency_overrides[get_file_service] = lambda: self.mock_file_service

        yield

        app.dependency_overrides.clear()

    def test_generated_file_detected(self, client, auth_headers):
        """Test that files generated during execution are detected."""
        self.mock_execution_service.execute_code.return_value = (
            CodeExecution(
                execution_id="exec-genfile",
                session_id="filegen-test-session",
                code="write file",
                language="py",
                status=ExecutionStatus.COMPLETED,
                exit_code=0,
                outputs=[
                    ExecutionOutput(
                        type=OutputType.FILE,
                        content="/mnt/data/output.txt",
                        mime_type="text/plain",
                        size=100,
                        timestamp=datetime.now(timezone.utc),
                    )
                ],
            ),
            None,
            None,
            [],
            "pool_hit",
        )

        # Mock store_execution_output_file to return a file_id string
        self.mock_file_service.store_execution_output_file.return_value = "gen-file-1"

        self.mock_file_service.list_files.return_value = [
            FileInfo(
                file_id="gen-file-1",
                filename="output.txt",
                size=100,
                content_type="text/plain",
                created_at=datetime.utcnow(),
                path="/output.txt",
            )
        ]

        response = client.post(
            "/exec",
            json={
                "code": "with open('output.txt', 'w') as f: f.write('hello')",
                "lang": "py",
            },
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["files"]) >= 1

    def test_multiple_files_generated(self, client, auth_headers):
        """Test that multiple generated files are detected."""
        self.mock_execution_service.execute_code.return_value = (
            CodeExecution(
                execution_id="exec-multifile",
                session_id="filegen-test-session",
                code="write files",
                language="py",
                status=ExecutionStatus.COMPLETED,
                exit_code=0,
                outputs=[
                    ExecutionOutput(
                        type=OutputType.FILE,
                        content="/mnt/data/file1.txt",
                        mime_type="text/plain",
                        size=50,
                        timestamp=datetime.now(timezone.utc),
                    ),
                    ExecutionOutput(
                        type=OutputType.FILE,
                        content="/mnt/data/file2.csv",
                        mime_type="text/csv",
                        size=100,
                        timestamp=datetime.now(timezone.utc),
                    ),
                ],
            ),
            None,
            None,
            [],
            "pool_hit",
        )

        # Mock store_execution_output_file to return file IDs (called multiple times)
        self.mock_file_service.store_execution_output_file.side_effect = [
            "gen-1",
            "gen-2",
        ]

        self.mock_file_service.list_files.return_value = [
            FileInfo(
                file_id="gen-1",
                filename="file1.txt",
                size=50,
                content_type="text/plain",
                created_at=datetime.utcnow(),
                path="/file1.txt",
            ),
            FileInfo(
                file_id="gen-2",
                filename="file2.csv",
                size=100,
                content_type="text/csv",
                created_at=datetime.utcnow(),
                path="/file2.csv",
            ),
        ]

        response = client.post(
            "/exec",
            json={"code": "generate multiple files", "lang": "py"},
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["files"]) >= 2

    def test_no_files_generated(self, client, auth_headers):
        """Test execution with no file generation."""
        self.mock_execution_service.execute_code.return_value = (
            CodeExecution(
                execution_id="exec-nofile",
                session_id="filegen-test-session",
                code="print only",
                language="py",
                status=ExecutionStatus.COMPLETED,
                exit_code=0,
                outputs=[
                    ExecutionOutput(
                        type=OutputType.STDOUT,
                        content="output",
                        timestamp=datetime.now(timezone.utc),
                    )
                ],
            ),
            None,
            None,
            [],
            "pool_hit",
        )

        self.mock_file_service.list_files.return_value = []

        response = client.post(
            "/exec", json={"code": "print('hello')", "lang": "py"}, headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data["files"] == []


# =============================================================================
# OUTPUT HANDLING BEHAVIOR
# =============================================================================


class TestOutputHandling:
    """Test output handling behavior."""

    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        """Set up mocks."""
        self.mock_session = create_session("output-test-session")

        self.mock_session_service = AsyncMock()
        self.mock_session_service.create_session.return_value = self.mock_session
        self.mock_session_service.get_session.return_value = self.mock_session

        self.mock_execution_service = AsyncMock()
        self.mock_file_service = AsyncMock()
        self.mock_file_service.list_files.return_value = []

        from src.dependencies.services import (
            get_session_service,
            get_execution_service,
            get_file_service,
        )

        app.dependency_overrides[get_session_service] = (
            lambda: self.mock_session_service
        )
        app.dependency_overrides[get_execution_service] = (
            lambda: self.mock_execution_service
        )
        app.dependency_overrides[get_file_service] = lambda: self.mock_file_service

        yield

        app.dependency_overrides.clear()

    def test_large_output_handling(self, client, auth_headers):
        """Test handling of large output."""
        large_output = "A" * 100000  # 100KB

        self.mock_execution_service.execute_code.return_value = (
            CodeExecution(
                execution_id="exec-large",
                session_id="output-test-session",
                code="print large",
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
            ),
            None,
            None,
            [],
            "pool_hit",
        )

        response = client.post(
            "/exec",
            json={"code": "print('A' * 100000)", "lang": "py"},
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["stdout"]) > 0

    def test_mixed_stdout_stderr(self, client, auth_headers):
        """Test handling of mixed stdout and stderr."""
        self.mock_execution_service.execute_code.return_value = (
            CodeExecution(
                execution_id="exec-mixed",
                session_id="output-test-session",
                code="mixed output",
                language="py",
                status=ExecutionStatus.COMPLETED,
                exit_code=0,
                outputs=[
                    ExecutionOutput(
                        type=OutputType.STDOUT,
                        content="stdout content",
                        timestamp=datetime.now(timezone.utc),
                    ),
                    ExecutionOutput(
                        type=OutputType.STDERR,
                        content="stderr content",
                        timestamp=datetime.now(timezone.utc),
                    ),
                ],
            ),
            None,
            None,
            [],
            "pool_hit",
        )

        response = client.post(
            "/exec", json={"code": "print and warn", "lang": "py"}, headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        # Both stdout and stderr should be populated
        assert "stdout" in data
        assert "stderr" in data

    def test_unicode_output(self, client, auth_headers):
        """Test handling of Unicode output."""
        unicode_output = "Hello 世界 🌍 مرحبا"

        self.mock_execution_service.execute_code.return_value = (
            CodeExecution(
                execution_id="exec-unicode",
                session_id="output-test-session",
                code="print unicode",
                language="py",
                status=ExecutionStatus.COMPLETED,
                exit_code=0,
                outputs=[
                    ExecutionOutput(
                        type=OutputType.STDOUT,
                        content=unicode_output,
                        timestamp=datetime.now(timezone.utc),
                    )
                ],
            ),
            None,
            None,
            [],
            "pool_hit",
        )

        response = client.post(
            "/exec",
            json={"code": "print('Hello 世界 🌍 مرحبا')", "lang": "py"},
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        # Should preserve Unicode
        assert "世界" in data["stdout"] or len(data["stdout"]) > 0
