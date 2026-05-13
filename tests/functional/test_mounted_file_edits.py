"""Functional tests for mounted file edit persistence against a live API.

Modified mounted files surface as new generated outputs with fresh file_ids
(not in-place overwrites of the original S3 object). The exec response
includes a `modified_from` reference back to the original upload. LibreChat
downloads the new file_id to capture the edited content.
"""

import pytest


def _find_modified_file(exec_result, original_file_id):
    """Find the generated file entry that was modified from the original."""
    for f in exec_result.get("files", []):
        modified_from = f.get("modified_from")
        if modified_from and modified_from.get("id") == original_file_id:
            return f
    return None


class TestMountedFileEdits:
    """Verify in-place edits to mounted files surface as new generated outputs."""

    @pytest.mark.asyncio
    async def test_overwrite_mounted_file_persists(self, async_client, auth_headers):
        """Overwriting a mounted file should produce a new output with modified content."""
        upload = await async_client.post(
            "/upload",
            headers={"x-api-key": auth_headers["x-api-key"]},
            files={"files": ("test.txt", b"original content", "text/plain")},
        )
        assert upload.status_code == 200, upload.text
        upload_result = upload.json()
        session_id = upload_result["storage_session_id"]
        file_id = upload_result["files"][0]["fileId"]

        execute = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "lang": "py",
                "code": (
                    "with open('/mnt/data/test.txt', 'w') as f:\n"
                    "    f.write('modified content')\n"
                    "print('File modified')\n"
                ),
                "session_id": session_id,
                "files": [
                    {"id": file_id, "storage_session_id": session_id, "name": "test.txt"}
                ],
            },
        )
        assert execute.status_code == 200, execute.text
        exec_result = execute.json()
        assert "File modified" in exec_result["stdout"]

        modified = _find_modified_file(exec_result, file_id)
        assert modified is not None, (
            f"No modified_from entry for {file_id} in files: {exec_result['files']}"
        )
        assert modified.get("inherited") is not True
        assert modified["name"] == "test.txt"

        download = await async_client.get(
            f"/download/{session_id}/{modified['id']}",
            headers=auth_headers,
        )
        assert download.status_code == 200
        assert download.text == "modified content"

    @pytest.mark.asyncio
    async def test_append_to_mounted_file_persists(self, async_client, auth_headers):
        """Appending to a mounted file should produce a new output with all lines."""
        upload = await async_client.post(
            "/upload",
            headers={"x-api-key": auth_headers["x-api-key"]},
            files={"files": ("log.txt", b"line1\n", "text/plain")},
        )
        assert upload.status_code == 200, upload.text
        upload_result = upload.json()
        session_id = upload_result["storage_session_id"]
        file_id = upload_result["files"][0]["fileId"]

        execute = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "lang": "py",
                "code": (
                    "with open('/mnt/data/log.txt', 'a') as f:\n"
                    "    f.write('line2\\n')\n"
                    "    f.write('line3\\n')\n"
                    "print('Appended')\n"
                ),
                "session_id": session_id,
                "files": [{"id": file_id, "storage_session_id": session_id, "name": "log.txt"}],
            },
        )
        assert execute.status_code == 200, execute.text
        exec_result = execute.json()

        modified = _find_modified_file(exec_result, file_id)
        assert modified is not None, (
            f"No modified_from entry for {file_id} in files: {exec_result['files']}"
        )
        assert modified.get("inherited") is not True

        download = await async_client.get(
            f"/download/{session_id}/{modified['id']}",
            headers=auth_headers,
        )
        assert download.status_code == 200
        assert "line1" in download.text
        assert "line2" in download.text
        assert "line3" in download.text

    @pytest.mark.asyncio
    async def test_delete_mounted_file_does_not_error(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Deleting a mounted file should not produce mounted-file update errors."""
        upload = await async_client.post(
            "/upload",
            headers={"x-api-key": auth_headers["x-api-key"]},
            files={"files": ("temp.txt", b"temporary content", "text/plain")},
            data={"entity_id": unique_entity_id},
        )
        assert upload.status_code == 200, upload.text
        upload_result = upload.json()
        session_id = upload_result["storage_session_id"]
        file_id = upload_result["files"][0]["fileId"]

        execute = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "lang": "py",
                "entity_id": unique_entity_id,
                "code": (
                    "import os\n"
                    "os.remove('/mnt/data/temp.txt')\n"
                    "print('File deleted')\n"
                ),
                "files": [
                    {"id": file_id, "storage_session_id": session_id, "name": "temp.txt"}
                ],
            },
        )
        assert execute.status_code == 200, execute.text
        execute_result = execute.json()
        assert "File deleted" in execute_result["stdout"]
        assert "Failed to update mounted file" not in execute_result["stderr"]

    @pytest.mark.asyncio
    async def test_edit_csv_file_persists(self, async_client, auth_headers):
        """Editing a mounted CSV file should produce a new output with transformed data."""
        upload = await async_client.post(
            "/upload",
            headers={"x-api-key": auth_headers["x-api-key"]},
            files={"files": ("data.csv", b"name,value\nAlice,10\nBob,20", "text/csv")},
        )
        assert upload.status_code == 200, upload.text
        upload_result = upload.json()
        session_id = upload_result["storage_session_id"]
        file_id = upload_result["files"][0]["fileId"]

        execute = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "lang": "py",
                "code": (
                    "import pandas as pd\n"
                    "df = pd.read_csv('/mnt/data/data.csv')\n"
                    "df['value'] = df['value'] * 2\n"
                    "df.to_csv('/mnt/data/data.csv', index=False)\n"
                    "print('csv updated')\n"
                ),
                "session_id": session_id,
                "files": [
                    {"id": file_id, "storage_session_id": session_id, "name": "data.csv"}
                ],
            },
        )
        assert execute.status_code == 200, execute.text
        exec_result = execute.json()
        assert "csv updated" in exec_result["stdout"]

        modified = _find_modified_file(exec_result, file_id)
        assert modified is not None, (
            f"No modified_from entry for {file_id} in files: {exec_result['files']}"
        )
        assert modified.get("inherited") is not True

        download = await async_client.get(
            f"/download/{session_id}/{modified['id']}",
            headers=auth_headers,
        )
        assert download.status_code == 200
        assert "Alice,20" in download.text
        assert "Bob,40" in download.text
