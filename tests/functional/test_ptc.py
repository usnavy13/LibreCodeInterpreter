"""Functional tests for Programmatic Tool Calling (PTC) against a live API endpoint."""

import pytest


class TestPTCInitialExecution:
    """Test POST /exec/programmatic with initial code execution."""

    @pytest.mark.asyncio
    async def test_ptc_simple_code_completes(
        self, async_client, auth_headers
    ):
        """PTC request with code that doesn't call any tools completes immediately."""
        response = await async_client.post(
            "/exec/programmatic",
            headers=auth_headers,
            json={
                "code": "print('hello from ptc')",
                "tools": [
                    {"name": "unused_tool", "description": "Not called"}
                ],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert "session_id" in data
        assert "hello from ptc" in data["stdout"]

    @pytest.mark.asyncio
    async def test_ptc_response_has_all_fields(
        self, async_client, auth_headers
    ):
        """PTC response includes all expected fields."""
        response = await async_client.post(
            "/exec/programmatic",
            headers=auth_headers,
            json={
                "code": "x = 1 + 1",
                "tools": [],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "session_id" in data
        assert "continuation_token" in data
        assert "tool_calls" in data
        assert "stdout" in data
        assert "stderr" in data
        assert "files" in data
        assert "error" in data

    @pytest.mark.asyncio
    async def test_ptc_no_code_returns_error(
        self, async_client, auth_headers
    ):
        """PTC request without code or continuation_token returns error."""
        response = await async_client.post(
            "/exec/programmatic",
            headers=auth_headers,
            json={},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"
        assert data["error"] is not None


class TestPTCToolCallFlow:
    """Test the full PTC tool call round-trip: code calls tool, we supply result."""

    @pytest.mark.asyncio
    async def test_ptc_tool_call_and_continuation(
        self, async_client, auth_headers
    ):
        """Full PTC round-trip: code calls a tool, receives result, completes."""
        # Step 1: Send code that calls a tool
        initial_response = await async_client.post(
            "/exec/programmatic",
            headers=auth_headers,
            json={
                "code": (
                    "result = await get_number()\n"
                    "print(f'got: {result}')"
                ),
                "tools": [
                    {
                        "name": "get_number",
                        "description": "Returns a number",
                        "parameters": {"type": "object", "properties": {}},
                    }
                ],
            },
        )

        assert initial_response.status_code == 200
        data = initial_response.json()
        assert data["status"] == "tool_call_required", (
            f"Expected tool_call_required, got {data['status']}. "
            f"stderr: {data.get('stderr', '')}, error: {data.get('error', '')}"
        )
        assert data["continuation_token"] is not None
        assert len(data["tool_calls"]) >= 1

        tool_call = data["tool_calls"][0]
        assert "id" in tool_call
        assert tool_call["name"] == "get_number"
        assert "input" in tool_call

        # Step 2: Send tool result back
        continuation_response = await async_client.post(
            "/exec/programmatic",
            headers=auth_headers,
            json={
                "continuation_token": data["continuation_token"],
                "tool_results": [
                    {
                        "call_id": tool_call["id"],
                        "result": 42,
                        "is_error": False,
                    }
                ],
            },
        )

        assert continuation_response.status_code == 200
        result = continuation_response.json()
        assert result["status"] == "completed"
        assert "got: 42" in result["stdout"]

    @pytest.mark.asyncio
    async def test_ptc_tool_with_arguments(
        self, async_client, auth_headers
    ):
        """Tool call passes arguments correctly."""
        initial = await async_client.post(
            "/exec/programmatic",
            headers=auth_headers,
            json={
                "code": (
                    "result = await add(a=3, b=7)\n"
                    "print(f'sum={result}')"
                ),
                "tools": [
                    {
                        "name": "add",
                        "description": "Add two numbers",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "a": {"type": "integer"},
                                "b": {"type": "integer"},
                            },
                        },
                    }
                ],
            },
        )

        assert initial.status_code == 200
        data = initial.json()
        assert data["status"] == "tool_call_required"

        tool_call = data["tool_calls"][0]
        assert tool_call["name"] == "add"
        assert tool_call["input"]["a"] == 3
        assert tool_call["input"]["b"] == 7

        # Return sum
        cont = await async_client.post(
            "/exec/programmatic",
            headers=auth_headers,
            json={
                "continuation_token": data["continuation_token"],
                "tool_results": [
                    {
                        "call_id": tool_call["id"],
                        "result": 10,
                        "is_error": False,
                    }
                ],
            },
        )

        assert cont.status_code == 200
        result = cont.json()
        assert result["status"] == "completed"
        assert "sum=10" in result["stdout"]

    @pytest.mark.asyncio
    async def test_ptc_tool_error_result(
        self, async_client, auth_headers
    ):
        """Tool result with is_error=true is handled by the code."""
        initial = await async_client.post(
            "/exec/programmatic",
            headers=auth_headers,
            json={
                "code": (
                    "try:\n"
                    "    result = await failing_tool()\n"
                    "    print(f'unexpected: {result}')\n"
                    "except Exception as e:\n"
                    "    print(f'caught: {e}')"
                ),
                "tools": [
                    {"name": "failing_tool", "description": "Will fail"}
                ],
            },
        )

        assert initial.status_code == 200
        data = initial.json()
        assert data["status"] == "tool_call_required"

        tool_call = data["tool_calls"][0]

        cont = await async_client.post(
            "/exec/programmatic",
            headers=auth_headers,
            json={
                "continuation_token": data["continuation_token"],
                "tool_results": [
                    {
                        "call_id": tool_call["id"],
                        "result": None,
                        "is_error": True,
                        "error_message": "Service unavailable",
                    }
                ],
            },
        )

        assert cont.status_code == 200
        result = cont.json()
        # Code should have caught the error or completed with error info
        assert result["status"] in ("completed", "error")


class TestPTCInvalidToken:
    """Test PTC continuation with invalid/expired tokens."""

    @pytest.mark.asyncio
    async def test_ptc_invalid_continuation_token(
        self, async_client, auth_headers
    ):
        """Invalid continuation token returns error status."""
        response = await async_client.post(
            "/exec/programmatic",
            headers=auth_headers,
            json={
                "continuation_token": "nonexistent-token-xyz",
                "tool_results": [
                    {"call_id": "fake-call", "result": "data"}
                ],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"
        assert data["error"] is not None


class TestPTCAuth:
    """Test authentication on PTC endpoint."""

    @pytest.mark.asyncio
    async def test_ptc_no_auth_returns_401(self, async_client):
        """PTC request without auth returns 401."""
        response = await async_client.post(
            "/exec/programmatic",
            json={"code": "print('hello')"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_ptc_invalid_auth_returns_401(self, async_client):
        """PTC request with wrong API key returns 401."""
        response = await async_client.post(
            "/exec/programmatic",
            headers={"x-api-key": "wrong-key-12345"},
            json={"code": "print('hello')"},
        )
        assert response.status_code == 401
