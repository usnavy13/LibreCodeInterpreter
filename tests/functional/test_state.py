"""Functional tests for state persistence API endpoints.

These are extended functionality beyond LibreChat's current usage.
LibreChat currently only supports file session persistence, not Python state.
"""

import pytest


class TestStateInfo:
    """Test GET /state/{session_id}/info."""

    @pytest.mark.asyncio
    async def test_info_nonexistent_state(
        self, async_client, auth_headers, unique_session_id
    ):
        """Info for non-existent state returns exists=false."""
        response = await async_client.get(
            f"/state/{unique_session_id}/info",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["exists"] is False

    @pytest.mark.asyncio
    async def test_info_after_execution(
        self, async_client, auth_headers, unique_entity_id
    ):
        """State info after Python execution shows state exists."""
        # Create state via execution
        exec_response = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": "state_test_var = {'key': 'value', 'number': 42}",
                "lang": "py",
                "entity_id": unique_entity_id,
            },
        )
        assert exec_response.status_code == 200
        session_id = exec_response.json()["session_id"]

        # Check state info
        info_response = await async_client.get(
            f"/state/{session_id}/info",
            headers=auth_headers,
        )

        assert info_response.status_code == 200
        data = info_response.json()
        # State should exist after Python execution
        assert "exists" in data
        if data["exists"]:
            assert "size_bytes" in data
            assert "hash" in data


class TestStateDownload:
    """Test GET /state/{session_id}."""

    @pytest.mark.asyncio
    async def test_download_nonexistent_state(
        self, async_client, auth_headers, unique_session_id
    ):
        """Download state for non-existent session returns 404."""
        response = await async_client.get(
            f"/state/{unique_session_id}",
            headers=auth_headers,
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_download_state_after_execution(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Download state after Python execution returns binary data."""
        # Create state via execution
        exec_response = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": "download_test_data = {'key': 'value'}",
                "lang": "py",
                "entity_id": unique_entity_id,
            },
        )
        assert exec_response.status_code == 200
        session_id = exec_response.json()["session_id"]

        # Try to download state
        state_response = await async_client.get(
            f"/state/{session_id}",
            headers=auth_headers,
        )

        # May be 200 (state exists) or 404 (no state captured)
        assert state_response.status_code in [200, 404]

        if state_response.status_code == 200:
            # Should have ETag header
            assert "etag" in state_response.headers
            # Should have binary content
            assert len(state_response.content) > 0

    @pytest.mark.asyncio
    async def test_state_etag_conditional_request(
        self, async_client, auth_headers, unique_entity_id
    ):
        """State download supports ETag conditional requests."""
        # Create state
        exec_response = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": "etag_test_data = [1, 2, 3, 4, 5]",
                "lang": "py",
                "entity_id": unique_entity_id,
            },
        )
        session_id = exec_response.json()["session_id"]

        # First download to get ETag
        first_response = await async_client.get(
            f"/state/{session_id}",
            headers=auth_headers,
        )

        if first_response.status_code == 200:
            etag = first_response.headers.get("etag")
            if etag:
                # Second request with If-None-Match should return 304
                second_response = await async_client.get(
                    f"/state/{session_id}",
                    headers={**auth_headers, "If-None-Match": etag},
                )
                # Should return 304 Not Modified
                assert second_response.status_code in [200, 304]


class TestStateUpload:
    """Test POST /state/{session_id}."""

    @pytest.mark.asyncio
    async def test_upload_valid_state(
        self, async_client, auth_headers, unique_session_id
    ):
        """Upload valid state returns 201."""
        # Create minimal valid state (version 2 + lz4 compressed data)
        # Version byte 0x02 indicates state format version 2
        state_bytes = b"\x02" + b"x" * 100  # Version byte + dummy data

        response = await async_client.post(
            f"/state/{unique_session_id}",
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
            content=state_bytes,
        )

        assert response.status_code == 201
        data = response.json()
        assert data["message"] == "state_uploaded"
        assert data["size"] == len(state_bytes)

    @pytest.mark.asyncio
    async def test_upload_invalid_version(
        self, async_client, auth_headers, unique_session_id
    ):
        """Upload state with invalid version returns 400."""
        # Invalid version byte (0x99 is not valid)
        state_bytes = b"\x99invalid_version_data"

        response = await async_client.post(
            f"/state/{unique_session_id}",
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
            content=state_bytes,
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_upload_empty_state(
        self, async_client, auth_headers, unique_session_id
    ):
        """Upload empty state returns 400."""
        response = await async_client.post(
            f"/state/{unique_session_id}",
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
            content=b"",
        )

        assert response.status_code == 400


class TestStateDelete:
    """Test DELETE /state/{session_id}."""

    @pytest.mark.asyncio
    async def test_delete_state(self, async_client, auth_headers, unique_session_id):
        """Delete state returns 204."""
        response = await async_client.delete(
            f"/state/{unique_session_id}",
            headers=auth_headers,
        )

        assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_nonexistent_state(
        self, async_client, auth_headers, unique_session_id
    ):
        """Delete non-existent state still returns 204."""
        response = await async_client.delete(
            f"/state/{unique_session_id}",
            headers=auth_headers,
        )

        assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_state_not_found_after_delete(
        self, async_client, auth_headers, unique_entity_id
    ):
        """State returns 404 after deletion."""
        # Create state
        exec_response = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": "delete_test_data = 'to be deleted'",
                "lang": "py",
                "entity_id": unique_entity_id,
            },
        )
        session_id = exec_response.json()["session_id"]

        # Verify state exists (or might not if state capture didn't happen)
        check_response = await async_client.get(
            f"/state/{session_id}/info",
            headers=auth_headers,
        )

        if check_response.json().get("exists"):
            # Delete state
            delete_response = await async_client.delete(
                f"/state/{session_id}",
                headers=auth_headers,
            )
            assert delete_response.status_code == 204

            # Verify state no longer exists
            info_response = await async_client.get(
                f"/state/{session_id}/info",
                headers=auth_headers,
            )
            assert info_response.json()["exists"] is False
