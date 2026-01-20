"""Functional tests for code execution across all 12 supported languages."""

import time

import pytest


class TestLanguageExecution:
    """Test POST /exec for all supported languages."""

    @pytest.mark.asyncio
    async def test_language_execution(
        self, async_client, auth_headers, language_test_case, unique_entity_id
    ):
        """Test that each language executes and produces expected output."""
        start = time.perf_counter()

        response = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": language_test_case["code"],
                "lang": language_test_case["lang"],
                "entity_id": unique_entity_id,
                "user_id": "functional-test",
            },
        )

        latency = time.perf_counter() - start

        # Basic assertions
        assert response.status_code == 200, (
            f"Failed for {language_test_case['lang']}: {response.text}"
        )

        data = response.json()

        # Verify response has LibreChat-compatible fields
        assert "session_id" in data, "Response must have session_id"
        assert "stdout" in data, "Response must have stdout"
        assert "stderr" in data, "Response must have stderr"
        assert "files" in data, "Response must have files"

        # Verify types
        assert isinstance(data["session_id"], str)
        assert isinstance(data["stdout"], str)
        assert isinstance(data["stderr"], str)
        assert isinstance(data["files"], list)

        # Verify output contains expected substring (the sum result "55")
        assert language_test_case["expected_output"] in data["stdout"], (
            f"Expected '{language_test_case['expected_output']}' in stdout for "
            f"{language_test_case['lang']}, got: {data['stdout']}"
        )

        # Timing assertion: execution should complete within 30 seconds
        assert latency < 30.0, f"Execution took {latency:.1f}s, expected < 30s"


class TestPythonExecution:
    """Specific tests for Python execution features."""

    @pytest.mark.asyncio
    async def test_python_with_imports(self, async_client, auth_headers, unique_entity_id):
        """Test Python execution with standard library imports."""
        response = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": "import json; print(json.dumps({'ok': True}))",
                "lang": "py",
                "entity_id": unique_entity_id,
            },
        )

        assert response.status_code == 200
        stdout = response.json()["stdout"].lower()
        assert '{"ok": true}' in stdout or "{'ok': true}" in stdout.replace('"', "'")

    @pytest.mark.asyncio
    async def test_python_with_numpy(self, async_client, auth_headers, unique_entity_id):
        """Test Python execution with NumPy."""
        response = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": "import numpy as np; print(f'mean={np.mean([1,2,3,4,5])}')",
                "lang": "py",
                "entity_id": unique_entity_id,
            },
        )

        assert response.status_code == 200
        assert "mean=3.0" in response.json()["stdout"]

    @pytest.mark.asyncio
    async def test_python_error_in_stderr(self, async_client, auth_headers, unique_entity_id):
        """Test that Python errors appear in stderr, not as HTTP error."""
        response = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": "raise ValueError('test error')",
                "lang": "py",
                "entity_id": unique_entity_id,
            },
        )

        # CRITICAL: Should return 200, not 4xx/5xx (LibreChat compatibility)
        assert response.status_code == 200
        data = response.json()
        assert "ValueError" in data["stderr"] or "test error" in data["stderr"]
