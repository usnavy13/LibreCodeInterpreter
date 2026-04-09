"""Live client replay tests derived from current librechat-agents behavior."""

import json
import time

import httpx
import pytest


def _normalize_artifact_files(result: dict) -> list[dict]:
    """Normalize exec artifact files into runtime-style injected refs."""
    session_id = result["session_id"]
    return [
        {
            "session_id": file_info.get("session_id") or session_id,
            "id": file_info["id"],
            "name": file_info["name"],
        }
        for file_info in result.get("files", [])
    ]


async def _fetch_runtime_file_refs(
    async_client, auth_headers, session_id: str
) -> list[dict]:
    """Mirror librechat-agents /files?detail=full fallback behavior."""
    response = await async_client.get(
        f"/files/{session_id}?detail=full",
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text

    file_references = []
    for file_info in response.json():
        name_parts = file_info["name"].split("/")
        file_id = name_parts[1].split(".")[0] if len(name_parts) > 1 else ""
        file_references.append(
            {
                "session_id": session_id,
                "id": file_id,
                "name": file_info["metadata"]["original-filename"],
            }
        )
    return file_references


async def _exec_like_runtime(
    async_client,
    auth_headers,
    *,
    code: str,
    session_id: str | None = None,
    injected_files: list[dict] | None = None,
    extra_fields: dict | None = None,
) -> dict:
    """Execute code the way the current direct runtime does."""
    payload = {
        "lang": "py",
        "code": code,
    }
    if extra_fields:
        payload.update(extra_fields)
    if session_id:
        payload["session_id"] = session_id

    file_references = injected_files
    if file_references is None and session_id:
        file_references = await _fetch_runtime_file_refs(
            async_client, auth_headers, session_id
        )
    if file_references:
        payload["files"] = file_references

    response = await async_client.post("/exec", headers=auth_headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


async def _start_ptc_like_runtime(
    async_client,
    auth_headers,
    *,
    code: str,
    tools: list[dict],
    session_id: str | None = None,
    injected_files: list[dict] | None = None,
    timeout: int | None = None,
) -> dict:
    """Start PTC the way the current programmatic runtime does."""
    payload = {
        "code": code,
        "tools": tools,
    }
    if session_id:
        payload["session_id"] = session_id
    if timeout is not None:
        payload["timeout"] = timeout

    file_references = injected_files
    if file_references is None and session_id:
        file_references = await _fetch_runtime_file_refs(
            async_client, auth_headers, session_id
        )
    if file_references:
        payload["files"] = file_references

    response = await async_client.post(
        "/exec/programmatic",
        headers=auth_headers,
        json=payload,
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestDirectClientReplay:
    """Replay the real direct execute_code client flow."""

    @pytest.mark.asyncio
    async def test_uploaded_files_follow_runtime_session_when_first_exec_has_no_outputs(
        self, async_client, auth_headers, unique_entity_id
    ):
        """An uploaded file should remain available after the first exec returns no artifacts."""
        upload = await async_client.post(
            "/upload",
            headers={"x-api-key": auth_headers["x-api-key"]},
            files={"files": ("report.csv", b"a,b\n1,2\n", "text/csv")},
            data={"entity_id": unique_entity_id},
        )
        assert upload.status_code == 200, upload.text
        upload_session_id = upload.json()["session_id"]
        upload_refs = await _fetch_runtime_file_refs(
            async_client, auth_headers, upload_session_id
        )

        first = await _exec_like_runtime(
            async_client,
            auth_headers,
            code="print(open('report.csv').read().strip())",
            injected_files=upload_refs,
        )
        assert "a,b" in first["stdout"]
        assert first["files"] == []

        second = await _exec_like_runtime(
            async_client,
            auth_headers,
            code="print(open('report.csv').read().strip())",
            session_id=first["session_id"],
        )
        assert "a,b" in second["stdout"]
        assert "1,2" in second["stdout"]

    @pytest.mark.asyncio
    async def test_uploaded_files_survive_runtime_fallback_after_outputs_are_generated(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Runtime fallback should surface both linked uploads and generated files."""
        upload = await async_client.post(
            "/upload",
            headers={"x-api-key": auth_headers["x-api-key"]},
            files={"files": ("report.csv", b"a,b\n1,2\n", "text/csv")},
            data={"entity_id": unique_entity_id},
        )
        assert upload.status_code == 200, upload.text
        upload_session_id = upload.json()["session_id"]
        upload_refs = await _fetch_runtime_file_refs(
            async_client, auth_headers, upload_session_id
        )

        first = await _exec_like_runtime(
            async_client,
            auth_headers,
            code=(
                "from pathlib import Path\n"
                "report = Path('/mnt/data/report.csv').read_text().strip()\n"
                "Path('/mnt/data/analysis.txt').write_text(f'copied:{report}')\n"
                "print(report)\n"
            ),
            injected_files=upload_refs,
        )
        assert "a,b" in first["stdout"]
        assert first["files"], "Expected generated output file from first execution"

        second = await _exec_like_runtime(
            async_client,
            auth_headers,
            code=(
                "print(open('report.csv').read().strip())\n"
                "print(open('analysis.txt').read().strip())\n"
            ),
            session_id=first["session_id"],
        )
        assert "a,b" in second["stdout"]
        assert "copied:a,b\n1,2" in second["stdout"]

    @pytest.mark.asyncio
    async def test_generated_files_replay_with_injected_files_and_fallback(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Generated files should work with both injected refs and /files fallback."""
        seed = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "lang": "py",
                "code": (
                    "with open('/mnt/data/analysis.txt', 'w') as f:\n"
                    "    f.write('seed-data')\n"
                    "print('seeded')\n"
                ),
                "entity_id": unique_entity_id,
                "user_id": "client-replay-direct",
            },
        )
        assert seed.status_code == 200, seed.text
        seed_result = seed.json()
        session_id = seed_result["session_id"]
        injected_files = _normalize_artifact_files(seed_result)
        assert injected_files, "Expected generated files from seed execution"

        replay_with_injected = await _exec_like_runtime(
            async_client,
            auth_headers,
            code="print(open('analysis.txt').read())",
            session_id=session_id,
            injected_files=injected_files,
            extra_fields={"user_id": "client-replay-direct"},
        )
        assert "seed-data" in replay_with_injected["stdout"]

        replay_with_fallback = await _exec_like_runtime(
            async_client,
            auth_headers,
            code="print(open('analysis.txt').read())",
            session_id=session_id,
            extra_fields={"user_id": "client-replay-direct"},
        )
        assert "seed-data" in replay_with_fallback["stdout"]

    @pytest.mark.asyncio
    async def test_same_user_file_refs_reuse_execution_session_and_state(
        self, async_client, auth_headers
    ):
        """Same-user file refs should reuse the prior execution session."""
        seed = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "lang": "py",
                "code": (
                    "x = 41\n"
                    "with open('/mnt/data/state.txt', 'w') as f:\n"
                    "    f.write('state file')\n"
                    "print('seeded')\n"
                ),
                "user_id": "same-user-replay",
            },
        )
        assert seed.status_code == 200, seed.text
        seed_result = seed.json()
        session_id = seed_result["session_id"]
        file_refs = _normalize_artifact_files(seed_result)
        assert file_refs, "Expected generated files from seed execution"

        replay = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "lang": "py",
                "code": "print(x + 1)\nprint(open('state.txt').read())",
                "user_id": "same-user-replay",
                "files": file_refs,
            },
        )
        assert replay.status_code == 200, replay.text
        replay_result = replay.json()

        assert replay_result["session_id"] == session_id
        assert "42" in replay_result["stdout"]
        assert "state file" in replay_result["stdout"]


class TestProgrammaticClientReplay:
    """Replay the real run_tools_with_code flow."""

    @pytest.mark.asyncio
    async def test_ptc_fallback_refs_preserve_files_after_continuation(
        self, async_client, auth_headers, unique_entity_id
    ):
        """PTC fallback refs should survive a tool pause/resume cycle."""
        upload = await async_client.post(
            "/upload",
            headers={"x-api-key": auth_headers["x-api-key"]},
            files={"files": ("report.csv", b"a,b\n1,2\n", "text/csv")},
            data={"entity_id": unique_entity_id},
        )
        assert upload.status_code == 200, upload.text
        upload_result = upload.json()
        session_id = upload_result["session_id"]

        initial = await _start_ptc_like_runtime(
            async_client,
            auth_headers,
            session_id=session_id,
            code=(
                "suffix = await get_suffix()\n"
                "from pathlib import Path\n"
                "print(Path('/mnt/data/report.csv').read_text().strip())\n"
                "print(f'suffix={suffix}')\n"
            ),
            tools=[
                {
                    "name": "get_suffix",
                    "description": "Return a suffix",
                    "parameters": {"type": "object", "properties": {}},
                }
            ],
        )
        assert initial["status"] == "tool_call_required"

        continuation = await async_client.post(
            "/exec/programmatic",
            headers=auth_headers,
            json={
                "continuation_token": initial["continuation_token"],
                "tool_results": [
                    {
                        "call_id": initial["tool_calls"][0]["id"],
                        "result": "done",
                        "is_error": False,
                    }
                ],
            },
        )
        assert continuation.status_code == 200, continuation.text
        result = continuation.json()

        assert result["status"] == "completed"
        assert "a,b" in result["stdout"]
        assert "1,2" in result["stdout"]
        assert "suffix=done" in result["stdout"]

    @pytest.mark.asyncio
    async def test_ptc_supports_multiple_round_trips(self, async_client, auth_headers):
        """Sequential awaits should produce multiple tool-call rounds."""
        initial = await _start_ptc_like_runtime(
            async_client,
            auth_headers,
            code=(
                "first = await get_number()\n"
                "second = await get_word()\n"
                "print(f'{first}-{second}')\n"
            ),
            tools=[
                {
                    "name": "get_number",
                    "description": "Return a number",
                    "parameters": {"type": "object", "properties": {}},
                },
                {
                    "name": "get_word",
                    "description": "Return a word",
                    "parameters": {"type": "object", "properties": {}},
                },
            ],
        )
        assert initial["status"] == "tool_call_required"
        assert initial["tool_calls"][0]["name"] == "get_number"

        second = await async_client.post(
            "/exec/programmatic",
            headers=auth_headers,
            json={
                "continuation_token": initial["continuation_token"],
                "tool_results": [
                    {
                        "call_id": initial["tool_calls"][0]["id"],
                        "result": 7,
                        "is_error": False,
                    }
                ],
            },
        )
        assert second.status_code == 200, second.text
        second_result = second.json()
        assert second_result["status"] == "tool_call_required"
        assert second_result["tool_calls"][0]["name"] == "get_word"

        final = await async_client.post(
            "/exec/programmatic",
            headers=auth_headers,
            json={
                "continuation_token": second_result["continuation_token"],
                "tool_results": [
                    {
                        "call_id": second_result["tool_calls"][0]["id"],
                        "result": "complete",
                        "is_error": False,
                    }
                ],
            },
        )
        assert final.status_code == 200, final.text
        final_result = final.json()

        assert final_result["status"] == "completed"
        assert "7-complete" in final_result["stdout"]


class TestExecStreamingReplay:
    """Replay the transport expectations of the Node-based client."""

    @pytest.mark.asyncio
    async def test_exec_stream_sends_keepalive_before_final_json(
        self, async_client, auth_headers
    ):
        """Long exec requests should emit keepalive whitespace on unbuffered transports."""
        timeout = httpx.Timeout(15.0, connect=5.0, read=10.0, write=5.0, pool=5.0)
        start = time.perf_counter()
        chunks = []
        first_chunk_latency = None

        async with async_client.stream(
            "POST",
            "/exec",
            headers=auth_headers,
            json={
                "lang": "py",
                "code": (
                    "import time\n" "time.sleep(5)\n" "print('stream complete')\n"
                ),
            },
            timeout=timeout,
        ) as response:
            assert response.status_code == 200

            async for chunk in response.aiter_raw():
                if not chunk:
                    continue
                chunks.append(chunk)
                if first_chunk_latency is None:
                    first_chunk_latency = time.perf_counter() - start

        assert first_chunk_latency is not None, "Expected at least one streamed chunk"
        body = b"".join(chunks).decode()
        payload = json.loads(body.lstrip())
        assert "stream complete" in payload["stdout"]

        if async_client.base_url.scheme == "http":
            assert (
                first_chunk_latency < 4.5
            ), f"First keepalive arrived after {first_chunk_latency:.2f}s"
            assert body.startswith(" "), "Expected leading whitespace keepalive chunk"
