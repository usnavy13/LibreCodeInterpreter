"""Functional tests for timing and performance assertions."""

import time

import pytest


class TestExecutionTiming:
    """Test execution timing constraints."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "lang,code",
        [
            ("py", "print('timing test')"),
            ("js", "console.log('timing test');"),
            (
                "go",
                'package main\nimport "fmt"\nfunc main() { fmt.Println("timing test") }',
            ),
        ],
    )
    async def test_simple_execution_under_30s(
        self, async_client, auth_headers, unique_entity_id, lang, code
    ):
        """Simple execution completes within 30 seconds."""
        start = time.perf_counter()
        response = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": code,
                "lang": lang,
                "entity_id": f"{unique_entity_id}-{lang}",
            },
        )
        latency = time.perf_counter() - start

        assert response.status_code == 200
        assert latency < 30.0, f"{lang} execution took {latency:.1f}s, expected < 30s"


class TestHealthTiming:
    """Test health endpoint timing."""

    @pytest.mark.asyncio
    async def test_health_under_1s(self, async_client):
        """Health check responds within 1 second."""
        start = time.perf_counter()
        response = await async_client.get("/health")
        latency = time.perf_counter() - start

        assert response.status_code == 200
        assert latency < 1.0, f"Health check took {latency:.2f}s, expected < 1s"

    @pytest.mark.asyncio
    async def test_detailed_health_under_5s(self, async_client, auth_headers):
        """Detailed health check responds within 5 seconds."""
        start = time.perf_counter()
        response = await async_client.get("/health/detailed", headers=auth_headers)
        latency = time.perf_counter() - start

        assert response.status_code in [200, 503]
        assert latency < 5.0, f"Detailed health took {latency:.2f}s, expected < 5s"


class TestFileTiming:
    """Test file operation timing."""

    @pytest.mark.asyncio
    async def test_upload_under_10s(self, async_client, auth_headers, unique_entity_id):
        """File upload completes within 10 seconds."""
        content = b"x" * 1024 * 100  # 100KB
        files = {"files": ("timing-test.txt", content, "text/plain")}

        start = time.perf_counter()
        response = await async_client.post(
            "/upload",
            headers={"x-api-key": auth_headers["x-api-key"]},
            files=files,
            data={"entity_id": unique_entity_id},
        )
        latency = time.perf_counter() - start

        assert response.status_code == 200
        assert latency < 10.0, f"Upload took {latency:.1f}s, expected < 10s"

    @pytest.mark.asyncio
    async def test_download_under_5s(
        self, async_client, auth_headers, unique_entity_id
    ):
        """File download completes within 5 seconds."""
        # Upload first
        content = b"download timing test content"
        files = {"files": ("download-timing.txt", content, "text/plain")}

        upload = await async_client.post(
            "/upload",
            headers={"x-api-key": auth_headers["x-api-key"]},
            files=files,
            data={"entity_id": unique_entity_id},
        )
        session_id = upload.json()["session_id"]
        file_id = upload.json()["files"][0]["fileId"]

        # Time the download
        start = time.perf_counter()
        response = await async_client.get(
            f"/download/{session_id}/{file_id}",
            headers=auth_headers,
        )
        latency = time.perf_counter() - start

        assert response.status_code == 200
        assert latency < 5.0, f"Download took {latency:.1f}s, expected < 5s"


class TestStateTiming:
    """Test state operation timing."""

    @pytest.mark.asyncio
    async def test_state_info_under_2s(
        self, async_client, auth_headers, unique_session_id
    ):
        """State info check responds within 2 seconds."""
        start = time.perf_counter()
        response = await async_client.get(
            f"/state/{unique_session_id}/info",
            headers=auth_headers,
        )
        latency = time.perf_counter() - start

        assert response.status_code == 200
        assert latency < 2.0, f"State info took {latency:.1f}s, expected < 2s"
