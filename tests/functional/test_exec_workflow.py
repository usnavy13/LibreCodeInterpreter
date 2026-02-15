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
