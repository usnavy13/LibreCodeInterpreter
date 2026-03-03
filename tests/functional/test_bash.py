"""Functional tests for bash execution against a live API endpoint."""

import pytest


class TestBashExecution:
    """Test bash code execution via POST /exec with lang='bash'."""

    @pytest.mark.asyncio
    async def test_bash_echo(self, async_client, auth_headers, unique_entity_id):
        """Basic bash echo returns expected output."""
        response = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": "echo hello-from-bash",
                "lang": "bash",
                "entity_id": unique_entity_id,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "hello-from-bash" in data["stdout"]

    @pytest.mark.asyncio
    async def test_bash_response_has_librechat_fields(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Bash response has the same 4 required LibreChat fields as Python."""
        response = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": "echo ok",
                "lang": "bash",
                "entity_id": unique_entity_id,
            },
        )

        assert response.status_code == 200
        data = response.json()

        assert "session_id" in data
        assert "stdout" in data
        assert "stderr" in data
        assert "files" in data

        assert isinstance(data["session_id"], str)
        assert isinstance(data["stdout"], str)
        assert isinstance(data["stderr"], str)
        assert isinstance(data["files"], list)

    @pytest.mark.asyncio
    async def test_bash_error_returns_200(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Bash syntax error returns HTTP 200 with error in stderr."""
        response = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": "if then fi done",
                "lang": "bash",
                "entity_id": unique_entity_id,
            },
        )

        # CRITICAL: Execution errors return 200 (LibreChat compatibility)
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert len(data["stderr"]) > 0

    @pytest.mark.asyncio
    async def test_bash_variables_and_arithmetic(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Bash arithmetic and variable expansion works."""
        response = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": 'x=42; echo "result=$((x * 2))"',
                "lang": "bash",
                "entity_id": unique_entity_id,
            },
        )

        assert response.status_code == 200
        assert "result=84" in response.json()["stdout"]

    @pytest.mark.asyncio
    async def test_bash_multiline_script(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Multi-line bash script with loops and conditionals."""
        code = (
            "total=0\n"
            "for i in 1 2 3 4 5; do\n"
            "  total=$((total + i))\n"
            "done\n"
            'echo "sum=$total"'
        )
        response = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": code,
                "lang": "bash",
                "entity_id": unique_entity_id,
            },
        )

        assert response.status_code == 200
        assert "sum=15" in response.json()["stdout"]

    @pytest.mark.asyncio
    async def test_bash_exit_code_nonzero(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Bash script with non-zero exit code still returns 200."""
        response = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": "echo before-error; exit 1",
                "lang": "bash",
                "entity_id": unique_entity_id,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "before-error" in data["stdout"]

    @pytest.mark.asyncio
    async def test_bash_piped_commands(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Bash piped commands work correctly."""
        response = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": 'echo -e "cherry\\napple\\nbanana" | sort',
                "lang": "bash",
                "entity_id": unique_entity_id,
            },
        )

        assert response.status_code == 200
        stdout = response.json()["stdout"]
        lines = [l for l in stdout.strip().split("\n") if l]
        assert lines == ["apple", "banana", "cherry"]
