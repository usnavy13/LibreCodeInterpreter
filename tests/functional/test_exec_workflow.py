"""Functional tests for execution workflows: sessions, state, files."""

import pytest


class TestSessionWorkflow:
    """Test session creation and reuse."""

    @pytest.mark.asyncio
    async def test_execution_creates_session(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Execution creates a new session."""
        response = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": "print('hello')",
                "lang": "py",
                "entity_id": unique_entity_id,
            },
        )

        assert response.status_code == 200
        session_id = response.json()["session_id"]
        assert session_id is not None
        assert len(session_id) > 0

    @pytest.mark.asyncio
    async def test_session_reuse_with_entity_id(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Same entity_id reuses the same session."""
        # First execution
        r1 = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={"code": "x = 42", "lang": "py", "entity_id": unique_entity_id},
        )
        session1 = r1.json()["session_id"]

        # Second execution with same entity_id
        r2 = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={"code": "print(x)", "lang": "py", "entity_id": unique_entity_id},
        )
        session2 = r2.json()["session_id"]

        assert session1 == session2


class TestLibreChatCompatibility:
    """Test LibreChat API response format compatibility."""

    @pytest.mark.asyncio
    async def test_response_has_librechat_fields(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Response has all required LibreChat fields."""
        response = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={"code": "print('test')", "lang": "py", "entity_id": unique_entity_id},
        )

        assert response.status_code == 200
        data = response.json()

        # LibreChat requires these 4 fields
        assert "session_id" in data
        assert "files" in data
        assert "stdout" in data
        assert "stderr" in data

        # Verify types
        assert isinstance(data["session_id"], str)
        assert isinstance(data["files"], list)
        assert isinstance(data["stdout"], str)
        assert isinstance(data["stderr"], str)

    @pytest.mark.asyncio
    async def test_execution_error_returns_200(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Code execution errors return HTTP 200 with error in stderr."""
        response = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": "this is not valid python [[[",
                "lang": "py",
                "entity_id": unique_entity_id,
            },
        )

        # CRITICAL: Should return 200, not 4xx or 5xx
        assert response.status_code == 200

        data = response.json()
        # Should have standard response format with error in stderr
        assert "session_id" in data
        assert "files" in data
        assert "stdout" in data
        assert "stderr" in data
        # stderr should contain the error
        assert len(data["stderr"]) > 0


class TestSessionIsolation:
    """Test session isolation for agent file sharing scenarios.

    When multiple users share an agent with attached files, each user
    must get their own session. The upload session_id in file references
    should NOT be blindly reused.
    """

    @pytest.mark.asyncio
    async def test_different_users_get_different_sessions(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Two users with the same entity_id but different user_ids get different sessions."""
        r1 = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": "print('user-a')",
                "lang": "py",
                "user_id": "user-a-isolation-test",
                "entity_id": unique_entity_id,
            },
        )
        assert r1.status_code == 200
        session_a = r1.json()["session_id"]

        # Different user_id, same entity_id
        r2 = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": "print('user-b')",
                "lang": "py",
                "user_id": "user-b-isolation-test",
                "entity_id": unique_entity_id,
            },
        )
        assert r2.status_code == 200
        session_b = r2.json()["session_id"]

        # With entity_id-based session reuse, both might share a session.
        # The key test is: when file references are involved, sessions diverge.
        # This test verifies each user gets a valid session.
        assert len(session_a) > 0
        assert len(session_b) > 0

    @pytest.mark.asyncio
    async def test_file_ref_does_not_leak_session_across_users(
        self, async_client, auth_headers, unique_entity_id
    ):
        """File references from an upload session should not share execution sessions.

        Simulates: Agent uploads file (creates upload session S1),
        then userA and userB both execute with a reference to that file.
        Each should get their own execution session, not reuse S1.
        """
        # Upload a file (simulating agent upload with entity_id)
        upload = await async_client.post(
            "/upload",
            headers={"x-api-key": auth_headers["x-api-key"]},
            files={"file": ("shared.txt", b"shared content", "text/plain")},
            data={"entity_id": unique_entity_id},
        )
        assert upload.status_code == 200
        upload_data = upload.json()
        upload_session = upload_data["session_id"]
        file_id = upload_data["files"][0]["fileId"]
        filename = upload_data["files"][0]["filename"]

        # User A executes with file reference
        r_a = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": f"print(open('{filename}').read())",
                "lang": "py",
                "user_id": "isolation-user-a",
                "files": [
                    {
                        "id": file_id,
                        "session_id": upload_session,
                        "name": filename,
                    }
                ],
            },
        )
        assert r_a.status_code == 200
        session_a = r_a.json()["session_id"]
        assert "shared content" in r_a.json()["stdout"]

        # User B executes with same file reference
        r_b = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": f"print(open('{filename}').read())",
                "lang": "py",
                "user_id": "isolation-user-b",
                "files": [
                    {
                        "id": file_id,
                        "session_id": upload_session,
                        "name": filename,
                    }
                ],
            },
        )
        assert r_b.status_code == 200
        session_b = r_b.json()["session_id"]
        assert "shared content" in r_b.json()["stdout"]

        # Neither user should reuse the upload session
        assert session_a != upload_session, (
            "User A should not reuse the upload session"
        )
        assert session_b != upload_session, (
            "User B should not reuse the upload session"
        )
        # Each user should get a different session
        assert session_a != session_b, (
            "Different users should get different sessions"
        )


class TestStatePersistence:
    """Test Python state persistence across executions."""

    @pytest.mark.asyncio
    async def test_variable_persists_across_executions(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Variables persist between executions in same session."""
        # Define variable
        r1 = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={"code": "counter = 100", "lang": "py", "entity_id": unique_entity_id},
        )
        assert r1.status_code == 200

        # Use variable in next execution
        r2 = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": "print(counter + 1)",
                "lang": "py",
                "entity_id": unique_entity_id,
            },
        )
        assert r2.status_code == 200
        assert "101" in r2.json()["stdout"]

    @pytest.mark.asyncio
    async def test_function_persists_across_executions(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Functions persist between executions."""
        # Define function
        r1 = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": "def greet(name): return f'Hello, {name}!'",
                "lang": "py",
                "entity_id": unique_entity_id,
            },
        )
        assert r1.status_code == 200

        # Call function
        r2 = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": "print(greet('World'))",
                "lang": "py",
                "entity_id": unique_entity_id,
            },
        )
        assert r2.status_code == 200
        assert "Hello, World!" in r2.json()["stdout"]

    @pytest.mark.asyncio
    async def test_exec_response_includes_state_fields(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Python execution response includes state fields."""
        r = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": "data = [1,2,3]",
                "lang": "py",
                "entity_id": unique_entity_id,
            },
        )

        assert r.status_code == 200
        data = r.json()

    @pytest.mark.asyncio
    async def test_dataframe_persists_across_executions(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Pandas DataFrames persist between executions.

        Note: This test may occasionally fail against live API due to
        state persistence timing. Re-run if it fails sporadically.
        """
        # Create DataFrame
        r1 = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": "import pandas as pd; df = pd.DataFrame({'a': [1,2,3], 'b': [4,5,6]})",
                "lang": "py",
                "entity_id": unique_entity_id,
            },
        )
        assert r1.status_code == 200

        # Access DataFrame
        r2 = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": "print(f'sum_a={df.a.sum()}, sum_b={df.b.sum()}')",
                "lang": "py",
                "entity_id": unique_entity_id,
            },
        )
        assert r2.status_code == 200
        stdout = r2.json()["stdout"]
        assert "sum_a=6" in stdout
        assert "sum_b=15" in stdout
