"""Functional tests for concurrent execution with large file uploads.

Regression test for event loop blocking bug: when large files (>40MB) are
downloaded from MinIO during file mounting, response.read() blocks the
asyncio event loop, starving all concurrent HTTP connections.

This manifests as "socket hang up" errors in clients like LibreChat.
"""

import asyncio
import time

import httpx
import pytest


# 50MB of CSV data — large enough to trigger measurable event loop blocking
LARGE_FILE_SIZE_MB = 50
LARGE_CSV_ROW = b"col1,col2,col3,col4,col5,col6,col7,col8\n"
LARGE_CSV_DATA = LARGE_CSV_ROW * (LARGE_FILE_SIZE_MB * 1024 * 1024 // len(LARGE_CSV_ROW))

# Threshold: concurrent pings must complete within this time (seconds).
# Without the fix, pings take 8-11s due to event loop blocking.
# Allow up to 8s to account for pool cold starts after container restart.
PING_MAX_LATENCY = 8.0


class TestConcurrentFileExecution:
    """Test that large file operations don't block the event loop."""

    @pytest.mark.asyncio
    async def test_large_file_exec_does_not_block_concurrent_requests(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Concurrent simple exec requests must not be blocked by large file mounting.

        Steps:
        1. Upload a 50MB CSV file
        2. Fire 5 exec requests referencing the file + 3 simple print() pings
        3. Assert all pings complete within PING_MAX_LATENCY seconds
        4. Assert all file execs succeed
        """
        # --- Upload large file ---
        upload_headers = {"x-api-key": auth_headers["x-api-key"]}
        files = {"files": ("large_test.csv", LARGE_CSV_DATA, "text/csv")}
        data = {"entity_id": unique_entity_id}

        upload_resp = await async_client.post(
            "/upload", headers=upload_headers, files=files, data=data,
            timeout=120.0,
        )
        assert upload_resp.status_code == 200, f"Upload failed: {upload_resp.text}"

        result = upload_resp.json()
        session_id = result["session_id"]
        file_id = result["files"][0]["fileId"]
        filename = result["files"][0]["filename"]

        # --- Define concurrent tasks ---
        async def exec_with_file(idx: int) -> tuple:
            """Execute code that references the large file."""
            start = time.perf_counter()
            resp = await async_client.post(
                "/exec",
                headers=auth_headers,
                json={
                    "code": f"import os; print('exec-{idx}', os.path.getsize('/mnt/data/{filename}'))",
                    "lang": "py",
                    "session_id": session_id,
                    "files": [
                        {"id": file_id, "session_id": session_id, "name": filename}
                    ],
                },
                timeout=60.0,
            )
            elapsed = time.perf_counter() - start
            return ("file_exec", idx, resp.status_code, elapsed)

        async def simple_ping(idx: int) -> tuple:
            """Simple exec with no file — should not be blocked."""
            start = time.perf_counter()
            resp = await async_client.post(
                "/exec",
                headers=auth_headers,
                json={
                    "code": f"print('ping-{idx}')",
                    "lang": "py",
                },
                timeout=60.0,
            )
            elapsed = time.perf_counter() - start
            return ("ping", idx, resp.status_code, elapsed)

        # --- Fire all concurrently ---
        tasks = []
        for i in range(5):
            tasks.append(exec_with_file(i))
        for i in range(3):
            tasks.append(simple_ping(i))

        results = await asyncio.gather(*tasks)

        # --- Assertions ---
        file_results = [r for r in results if r[0] == "file_exec"]
        ping_results = [r for r in results if r[0] == "ping"]

        # All requests must succeed
        for kind, idx, status, elapsed in results:
            assert status == 200, f"{kind}-{idx} failed with status {status}"

        # Pings must not be blocked by file operations
        for kind, idx, status, elapsed in ping_results:
            assert elapsed < PING_MAX_LATENCY, (
                f"ping-{idx} took {elapsed:.2f}s (limit: {PING_MAX_LATENCY}s) — "
                f"event loop likely blocked by large file I/O"
            )

        max_ping = max(r[3] for r in ping_results)
        avg_ping = sum(r[3] for r in ping_results) / len(ping_results)
        max_file = max(r[3] for r in file_results)

        # Log timing summary (visible with -s flag)
        print(f"\n  File exec max: {max_file:.2f}s")
        print(f"  Ping max: {max_ping:.2f}s, avg: {avg_ping:.2f}s")
        for kind, idx, status, elapsed in sorted(results, key=lambda r: r[3]):
            blocked = " *** BLOCKED" if elapsed > PING_MAX_LATENCY else ""
            print(f"    {kind}-{idx}: {elapsed:.2f}s{blocked}")
