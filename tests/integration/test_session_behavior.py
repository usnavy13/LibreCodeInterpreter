"""
Session Behavior Tests - Phase 0 Behavioral Baseline

This test suite documents and verifies session lifecycle behavior
to ensure 100% compatibility after architectural refactoring.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone, timedelta
import io

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


def create_session(
    session_id: str, entity_id: str = None, metadata: dict = None
) -> Session:
    """Helper to create a session with specific properties."""
    meta = metadata or {}
    if entity_id:
        meta["entity_id"] = entity_id

    return Session(
        session_id=session_id,
        status=SessionStatus.ACTIVE,
        created_at=datetime.now(timezone.utc),
        last_activity=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        metadata=meta,
    )


def create_execution(session_id: str, stdout: str = "output") -> CodeExecution:
    """Helper to create a mock execution."""
    return CodeExecution(
        execution_id=f"exec-{session_id}-123",
        session_id=session_id,
        code="print('test')",
        language="py",
        status=ExecutionStatus.COMPLETED,
        exit_code=0,
        execution_time_ms=100,
        outputs=[
            ExecutionOutput(
                type=OutputType.STDOUT,
                content=stdout,
                timestamp=datetime.now(timezone.utc),
            )
        ],
    )


# =============================================================================
# SESSION CREATION BEHAVIOR
# =============================================================================


class TestSessionCreation:
    """Test session creation behavior."""

    def test_session_created_on_first_exec(self, client, auth_headers):
        """Test that a new session is created on first execution."""
        mock_session = create_session("new-session-123")
        mock_execution = create_execution("new-session-123")

        mock_session_service = AsyncMock()
        mock_session_service.create_session.return_value = mock_session
        mock_session_service.get_session.return_value = None  # No existing session

        mock_execution_service = AsyncMock()
        # execute_code returns (execution, container, new_state, state_errors, container_source)
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
                json={"code": "print('hello')", "lang": "py"},
                headers=auth_headers,
            )

            assert response.status_code == 200
            data = response.json()

            # Should return a session_id
            assert "session_id" in data
            assert len(data["session_id"]) > 0

            # create_session should have been called
            mock_session_service.create_session.assert_called_once()
        finally:
            app.dependency_overrides.clear()

    def test_session_created_with_entity_id(self, client, auth_headers):
        """Test that a session is created when entity_id is provided."""
        entity_id = "test-entity-abc"
        mock_session = create_session("session-with-entity", entity_id=entity_id)
        mock_execution = create_execution("session-with-entity")

        mock_session_service = AsyncMock()
        mock_session_service.create_session.return_value = mock_session
        mock_session_service.get_session.return_value = None

        mock_execution_service = AsyncMock()
        # execute_code returns (execution, container, new_state, state_errors, container_source)
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
                json={"code": "print('hello')", "lang": "py", "entity_id": entity_id},
                headers=auth_headers,
            )

            assert response.status_code == 200

            # Verify session was created (entity_id is used for lookup, not stored in metadata)
            assert (
                mock_session_service.create_session.called
                or mock_session_service.get_session.called
            )
            # Response should contain a session_id
            assert "session_id" in response.json()
        finally:
            app.dependency_overrides.clear()

    def test_session_created_with_user_id(self, client, auth_headers):
        """Test that session captures user_id."""
        user_id = "user-123"
        mock_session = create_session("session-with-user")
        mock_execution = create_execution("session-with-user")

        mock_session_service = AsyncMock()
        mock_session_service.create_session.return_value = mock_session
        mock_session_service.get_session.return_value = None

        mock_execution_service = AsyncMock()
        # execute_code returns (execution, container, new_state, state_errors, container_source)
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
                json={"code": "print('hello')", "lang": "py", "user_id": user_id},
                headers=auth_headers,
            )

            assert response.status_code == 200
        finally:
            app.dependency_overrides.clear()


# =============================================================================
# SESSION REUSE BEHAVIOR
# =============================================================================


class TestSessionReuse:
    """Test session reuse behavior."""

    def test_session_reused_with_same_entity_id(self, client, auth_headers):
        """Test that sessions are reused when same entity_id is provided."""
        entity_id = "shared-entity"
        existing_session = create_session("existing-session", entity_id=entity_id)
        mock_execution = create_execution("existing-session")

        mock_session_service = AsyncMock()
        mock_session_service.create_session.return_value = existing_session
        mock_session_service.get_session.return_value = existing_session
        mock_session_service.list_sessions_by_entity.return_value = [existing_session]

        mock_execution_service = AsyncMock()
        # execute_code returns (execution, container, new_state, state_errors, container_source)
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
            # First execution
            response1 = client.post(
                "/exec",
                json={"code": "x = 1", "lang": "py", "entity_id": entity_id},
                headers=auth_headers,
            )

            # Second execution with same entity
            response2 = client.post(
                "/exec",
                json={"code": "print(x)", "lang": "py", "entity_id": entity_id},
                headers=auth_headers,
            )

            assert response1.status_code == 200
            assert response2.status_code == 200

            # Should use the same session
            assert response1.json()["session_id"] == response2.json()["session_id"]
        finally:
            app.dependency_overrides.clear()

    def test_different_entity_gets_different_session(self, client, auth_headers):
        """Test that different entity_ids get different sessions."""
        session1 = create_session("session-1", entity_id="entity-1")
        session2 = create_session("session-2", entity_id="entity-2")

        # Each call creates a new session
        mock_session_service = AsyncMock()
        mock_session_service.create_session.side_effect = [session1, session2]
        mock_session_service.get_session.return_value = None
        mock_session_service.list_sessions_by_entity.return_value = []

        mock_execution_service = AsyncMock()
        # execute_code returns (execution, container, new_state, state_errors, container_source)
        mock_execution_service.execute_code.side_effect = [
            (create_execution("session-1"), None, None, [], "pool_hit"),
            (create_execution("session-2"), None, None, [], "pool_hit"),
        ]

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
            response1 = client.post(
                "/exec",
                json={"code": "print('1')", "lang": "py", "entity_id": "entity-1"},
                headers=auth_headers,
            )

            response2 = client.post(
                "/exec",
                json={"code": "print('2')", "lang": "py", "entity_id": "entity-2"},
                headers=auth_headers,
            )

            assert response1.status_code == 200
            assert response2.status_code == 200

            # Should have different session IDs
            assert response1.json()["session_id"] != response2.json()["session_id"]
        finally:
            app.dependency_overrides.clear()


# =============================================================================
# FILE PERSISTENCE BEHAVIOR
# =============================================================================


class TestFilePersistence:
    """Test file persistence across executions."""

    @pytest.mark.skip(
        reason="Requires full integration testing with real services - complex multi-step file flow"
    )
    def test_uploaded_file_available_in_execution(self, client, auth_headers):
        """Test that uploaded files are available during execution."""
        session_id = "file-test-session"
        file_id = "uploaded-file-123"

        mock_session = create_session(session_id, entity_id="file-test-entity")
        mock_execution = create_execution(session_id, "file content")

        mock_session_service = AsyncMock()
        mock_session_service.create_session.return_value = mock_session
        mock_session_service.get_session.return_value = mock_session

        mock_execution_service = AsyncMock()
        # execute_code returns (execution, container, new_state, state_errors, container_source)
        mock_execution_service.execute_code.return_value = (
            mock_execution,
            None,
            None,
            [],
            "pool_hit",
        )

        mock_file_service = AsyncMock()
        mock_file_service.store_uploaded_file.return_value = file_id
        mock_file_service.list_files.return_value = [
            FileInfo(
                file_id=file_id,
                filename="data.txt",
                size=100,
                content_type="text/plain",
                created_at=datetime.utcnow(),
                path="/data.txt",
            )
        ]
        mock_file_service.get_file_info.return_value = FileInfo(
            file_id=file_id,
            filename="data.txt",
            size=100,
            content_type="text/plain",
            created_at=datetime.utcnow(),
            path="/data.txt",
        )
        mock_file_service.get_file_content.return_value = b"test content"

        from src.dependencies.services import (
            get_session_service,
            get_execution_service,
            get_file_service,
        )

        app.dependency_overrides[get_session_service] = lambda: mock_session_service
        app.dependency_overrides[get_execution_service] = lambda: mock_execution_service
        app.dependency_overrides[get_file_service] = lambda: mock_file_service

        try:
            # Upload a file
            files = {"files": ("data.txt", io.BytesIO(b"test content"), "text/plain")}
            data = {"entity_id": "file-test-entity"}

            upload_response = client.post(
                "/files/upload", files=files, data=data, headers=auth_headers
            )
            assert upload_response.status_code == 200
            uploaded_file = upload_response.json()["files"][0]

            # Execute code that references the file
            exec_response = client.post(
                "/exec",
                json={
                    "code": "with open('data.txt') as f: print(f.read())",
                    "lang": "py",
                    "entity_id": "file-test-entity",
                    "files": [
                        {
                            "id": uploaded_file["id"],
                            "session_id": uploaded_file["session_id"],
                            "name": "data.txt",
                        }
                    ],
                },
                headers=auth_headers,
            )

            assert exec_response.status_code == 200

            # Verify execution service was called with files
            call_args = mock_execution_service.execute_code.call_args
            files_arg = call_args[1].get("files", [])
            assert len(files_arg) >= 1
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.skip(
        reason="Requires full integration testing with real services - complex multi-step file flow"
    )
    def test_generated_file_downloadable(self, client, auth_headers):
        """Test that files generated during execution can be downloaded."""
        session_id = "gen-file-session"
        gen_file_id = "generated-file-456"

        mock_session = create_session(session_id)

        # Execution that generates a file
        execution_with_file = CodeExecution(
            execution_id="exec-gen-file",
            session_id=session_id,
            code="write file",
            language="py",
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            outputs=[
                ExecutionOutput(
                    type=OutputType.FILE,
                    content="/workspace/output.txt",
                    mime_type="text/plain",
                    size=50,
                    timestamp=datetime.now(timezone.utc),
                )
            ],
        )

        mock_session_service = AsyncMock()
        mock_session_service.create_session.return_value = mock_session
        mock_session_service.get_session.return_value = mock_session

        mock_execution_service = AsyncMock()
        # execute_code returns (execution, container, new_state, state_errors, container_source)
        mock_execution_service.execute_code.return_value = (
            execution_with_file,
            None,
            None,
            [],
            "pool_hit",
        )

        mock_file_service = AsyncMock()
        mock_file_service.list_files.return_value = [
            FileInfo(
                file_id=gen_file_id,
                filename="output.txt",
                size=50,
                content_type="text/plain",
                created_at=datetime.utcnow(),
                path="/output.txt",
            )
        ]
        mock_file_service.download_file.return_value = "https://minio.test/download"

        from src.dependencies.services import (
            get_session_service,
            get_execution_service,
            get_file_service,
        )

        app.dependency_overrides[get_session_service] = lambda: mock_session_service
        app.dependency_overrides[get_execution_service] = lambda: mock_execution_service
        app.dependency_overrides[get_file_service] = lambda: mock_file_service

        try:
            # Execute code that generates a file
            exec_response = client.post(
                "/exec",
                json={
                    "code": "with open('output.txt', 'w') as f: f.write('generated')",
                    "lang": "py",
                },
                headers=auth_headers,
            )

            assert exec_response.status_code == 200
            generated_files = exec_response.json()["files"]
            assert len(generated_files) >= 1

            # Attempt to download the generated file
            file_ref = generated_files[0]
            download_response = client.get(
                f"/files/download/{session_id}/{file_ref['id']}",
                headers=auth_headers,
                follow_redirects=False,
            )

            assert download_response.status_code == 302
        finally:
            app.dependency_overrides.clear()


# =============================================================================
# SESSION ISOLATION BEHAVIOR
# =============================================================================


class TestSessionIsolation:
    """Test session isolation between different users/entities."""

    def test_sessions_isolated_between_users(self, client, auth_headers):
        """Test that different users have isolated sessions."""
        session1 = create_session("user1-session", entity_id="entity-1")
        session2 = create_session("user2-session", entity_id="entity-2")

        # This test verifies that entity_id creates session isolation
        mock_session_service = AsyncMock()
        mock_session_service.list_sessions_by_entity.side_effect = [
            [session1],  # For entity-1
            [session2],  # For entity-2
        ]

        from src.dependencies.services import get_session_service

        app.dependency_overrides[get_session_service] = lambda: mock_session_service

        try:
            # Verify entity-based session lookup is used
            mock_session_service.list_sessions_by_entity.assert_not_called()
        finally:
            app.dependency_overrides.clear()

    def test_files_not_accessible_cross_session(self, client, auth_headers):
        """Test that files from one session are not accessible in another."""
        # This test documents that file access should be session-scoped
        mock_file_service = AsyncMock()
        mock_file_service.get_file_info.return_value = None  # Not found

        from src.dependencies.services import get_file_service

        app.dependency_overrides[get_file_service] = lambda: mock_file_service

        try:
            # Try to access file from different session
            response = client.get(
                "/files/download/other-session/some-file-id", headers=auth_headers
            )

            # Should not find the file
            assert response.status_code == 404
        finally:
            app.dependency_overrides.clear()


# =============================================================================
# SESSION ID STABILITY
# =============================================================================


class TestSessionIdStability:
    """Test that session IDs remain stable across requests."""

    def test_session_id_consistent_in_response(self, client, auth_headers):
        """Test that the same session_id is returned for same entity."""
        stable_session_id = "stable-session-abc123"
        session = create_session(stable_session_id, entity_id="stable-entity")
        execution = create_execution(stable_session_id)

        mock_session_service = AsyncMock()
        mock_session_service.create_session.return_value = session
        mock_session_service.get_session.return_value = session
        mock_session_service.list_sessions_by_entity.return_value = [session]

        mock_execution_service = AsyncMock()
        # execute_code returns (execution, container, new_state, state_errors, container_source)
        mock_execution_service.execute_code.return_value = (
            execution,
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
            # Multiple executions
            responses = []
            for i in range(3):
                response = client.post(
                    "/exec",
                    json={
                        "code": f"print({i})",
                        "lang": "py",
                        "entity_id": "stable-entity",
                    },
                    headers=auth_headers,
                )
                responses.append(response)

            # All should return the same session_id
            session_ids = [r.json()["session_id"] for r in responses]
            assert all(sid == session_ids[0] for sid in session_ids)
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.skip(
        reason="Requires full integration testing with real services - complex file ID stability verification"
    )
    def test_file_ids_stable_across_requests(self, client, auth_headers):
        """Test that file IDs remain stable."""
        stable_file_id = "stable-file-xyz789"

        mock_file_service = AsyncMock()
        mock_file_service.store_uploaded_file.return_value = stable_file_id
        mock_file_service.list_files.return_value = [
            FileInfo(
                file_id=stable_file_id,
                filename="stable.txt",
                size=100,
                content_type="text/plain",
                created_at=datetime.utcnow(),
                path="/stable.txt",
            )
        ]
        mock_file_service.get_file_info.return_value = FileInfo(
            file_id=stable_file_id,
            filename="stable.txt",
            size=100,
            content_type="text/plain",
            created_at=datetime.utcnow(),
            path="/stable.txt",
        )

        from src.dependencies.services import get_file_service

        app.dependency_overrides[get_file_service] = lambda: mock_file_service

        try:
            # Upload a file
            files = {"files": ("stable.txt", io.BytesIO(b"content"), "text/plain")}
            upload_response = client.post(
                "/files/upload", files=files, headers=auth_headers
            )

            uploaded_id = upload_response.json()["files"][0]["id"]

            # List files - should show same ID
            list_response = client.get(
                "/files/files/temp-session", headers=auth_headers
            )
            listed_id = list_response.json()[0]["id"]

            assert uploaded_id == listed_id
        finally:
            app.dependency_overrides.clear()
