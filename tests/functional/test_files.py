"""Functional tests for file management endpoints."""

import pytest


class TestFileUpload:
    """Test POST /upload."""

    @pytest.mark.asyncio
    async def test_upload_single_file(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Upload a single file using 'files' field."""
        files = {"files": ("test.txt", b"Hello World", "text/plain")}
        data = {"entity_id": unique_entity_id}

        response = await async_client.post(
            "/upload",
            headers={"x-api-key": auth_headers["x-api-key"]},
            files=files,
            data=data,
        )

        assert response.status_code == 200
        result = response.json()

        assert result["message"] == "success"
        assert "session_id" in result
        assert len(result["files"]) == 1
        assert "fileId" in result["files"][0]
        assert "filename" in result["files"][0]

    @pytest.mark.asyncio
    async def test_librechat_upload_format(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Test LibreChat 'file' (singular) field name."""
        # LibreChat uses 'file' singular
        files = {"file": ("document.pdf", b"PDF content here", "application/pdf")}
        data = {"entity_id": unique_entity_id}

        response = await async_client.post(
            "/upload",
            headers={"x-api-key": auth_headers["x-api-key"]},
            files=files,
            data=data,
        )

        assert response.status_code == 200
        assert response.json()["message"] == "success"

    @pytest.mark.asyncio
    async def test_upload_returns_session_id(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Upload response includes session_id."""
        files = {"files": ("test.txt", b"content", "text/plain")}
        data = {"entity_id": unique_entity_id}

        response = await async_client.post(
            "/upload",
            headers={"x-api-key": auth_headers["x-api-key"]},
            files=files,
            data=data,
        )

        result = response.json()
        assert "session_id" in result
        assert len(result["session_id"]) > 0

    @pytest.mark.asyncio
    async def test_upload_returns_file_info(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Upload response includes file info with fileId and filename."""
        files = {"files": ("myfile.csv", b"a,b,c\n1,2,3", "text/csv")}
        data = {"entity_id": unique_entity_id}

        response = await async_client.post(
            "/upload",
            headers={"x-api-key": auth_headers["x-api-key"]},
            files=files,
            data=data,
        )

        result = response.json()
        assert len(result["files"]) == 1
        file_info = result["files"][0]
        assert "fileId" in file_info
        assert "filename" in file_info
        assert file_info["filename"] == "myfile.csv"


class TestFileList:
    """Test GET /files/{session_id}."""

    @pytest.mark.asyncio
    async def test_list_files_empty_session(
        self, async_client, auth_headers, unique_session_id
    ):
        """List files for non-existent session returns empty array."""
        response = await async_client.get(
            f"/files/{unique_session_id}",
            headers=auth_headers,
        )

        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_list_files_after_upload(
        self, async_client, auth_headers, unique_entity_id
    ):
        """List files returns uploaded file info."""
        # First upload a file
        files = {"files": ("list-test.txt", b"content for list test", "text/plain")}
        upload = await async_client.post(
            "/upload",
            headers={"x-api-key": auth_headers["x-api-key"]},
            files=files,
            data={"entity_id": unique_entity_id},
        )
        session_id = upload.json()["session_id"]

        # List files
        response = await async_client.get(
            f"/files/{session_id}",
            headers=auth_headers,
        )

        assert response.status_code == 200
        files_list = response.json()
        assert len(files_list) >= 1

    @pytest.mark.asyncio
    async def test_list_files_detail_simple(
        self, async_client, auth_headers, unique_entity_id
    ):
        """List files with detail=simple returns minimal info."""
        # First upload a file
        files = {"files": ("simple-test.txt", b"content", "text/plain")}
        upload = await async_client.post(
            "/upload",
            headers={"x-api-key": auth_headers["x-api-key"]},
            files=files,
            data={"entity_id": unique_entity_id},
        )
        session_id = upload.json()["session_id"]

        # List with simple detail
        response = await async_client.get(
            f"/files/{session_id}?detail=simple",
            headers=auth_headers,
        )

        assert response.status_code == 200
        files_list = response.json()
        assert isinstance(files_list, list)

    @pytest.mark.asyncio
    async def test_list_files_detail_summary(
        self, async_client, auth_headers, unique_entity_id
    ):
        """List files with detail=summary returns summary info."""
        # First upload a file
        files = {"files": ("summary-test.txt", b"content", "text/plain")}
        upload = await async_client.post(
            "/upload",
            headers={"x-api-key": auth_headers["x-api-key"]},
            files=files,
            data={"entity_id": unique_entity_id},
        )
        session_id = upload.json()["session_id"]

        # List with summary detail
        response = await async_client.get(
            f"/files/{session_id}?detail=summary",
            headers=auth_headers,
        )

        assert response.status_code == 200
        files_list = response.json()
        assert isinstance(files_list, list)


class TestFileDownload:
    """Test GET /download/{session_id}/{file_id}."""

    @pytest.mark.asyncio
    async def test_download_uploaded_file(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Download uploaded file returns correct content."""
        content = b"Download test content - unique data 12345"
        files = {"files": ("download-test.txt", content, "text/plain")}

        upload = await async_client.post(
            "/upload",
            headers={"x-api-key": auth_headers["x-api-key"]},
            files=files,
            data={"entity_id": unique_entity_id},
        )

        session_id = upload.json()["session_id"]
        file_id = upload.json()["files"][0]["fileId"]

        response = await async_client.get(
            f"/download/{session_id}/{file_id}",
            headers=auth_headers,
        )

        assert response.status_code == 200
        assert response.content == content

    @pytest.mark.asyncio
    async def test_download_nonexistent_returns_404(
        self, async_client, auth_headers, unique_session_id
    ):
        """Download non-existent file returns 404."""
        response = await async_client.get(
            f"/download/{unique_session_id}/fake-file-id",
            headers=auth_headers,
        )

        assert response.status_code == 404


class TestFileExecutionIntegration:
    """Test the full upload → execute (read file) → generate output → download flow."""

    @pytest.mark.asyncio
    async def test_uploaded_file_readable_at_mnt_data(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Uploaded file is readable at /mnt/data/ inside execution sandbox."""
        csv_content = b"name,age,city\nAlice,30,NYC\nBob,25,LA\n"
        files = {"files": ("people.csv", csv_content, "text/csv")}

        # Upload
        upload = await async_client.post(
            "/upload",
            headers={"x-api-key": auth_headers["x-api-key"]},
            files=files,
            data={"entity_id": unique_entity_id},
        )
        assert upload.status_code == 200
        upload_data = upload.json()
        session_id = upload_data["session_id"]
        file_id = upload_data["files"][0]["fileId"]
        filename = upload_data["files"][0]["filename"]

        # Execute code that reads the file via /mnt/data/ path
        exec_response = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": (
                    "import csv\n"
                    f"with open('/mnt/data/{filename}') as f:\n"
                    "    reader = csv.DictReader(f)\n"
                    "    rows = list(reader)\n"
                    "print(len(rows))\n"
                    "print(rows[0]['name'])\n"
                ),
                "lang": "py",
                "session_id": session_id,
                "files": [
                    {"id": file_id, "session_id": session_id, "name": filename}
                ],
            },
        )

        assert exec_response.status_code == 200
        result = exec_response.json()
        assert "2" in result["stdout"]
        assert "Alice" in result["stdout"]
        assert result["stderr"] == ""

    @pytest.mark.asyncio
    async def test_uploaded_file_readable_via_relative_path(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Uploaded file is also readable via relative path (CWD = /mnt/data)."""
        content = b"hello from uploaded file"
        files = {"files": ("greeting.txt", content, "text/plain")}

        upload = await async_client.post(
            "/upload",
            headers={"x-api-key": auth_headers["x-api-key"]},
            files=files,
            data={"entity_id": unique_entity_id},
        )
        upload_data = upload.json()
        session_id = upload_data["session_id"]
        file_id = upload_data["files"][0]["fileId"]
        filename = upload_data["files"][0]["filename"]

        exec_response = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": f"print(open('{filename}').read())",
                "lang": "py",
                "session_id": session_id,
                "files": [
                    {"id": file_id, "session_id": session_id, "name": filename}
                ],
            },
        )

        result = exec_response.json()
        assert "hello from uploaded file" in result["stdout"]

    @pytest.mark.asyncio
    async def test_upload_execute_generate_download(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Full round-trip: upload CSV → process with pandas → download result."""
        csv_data = b"product,price\nWidget,9.99\nGadget,19.99\n"
        files = {"files": ("input.csv", csv_data, "text/csv")}

        # Upload
        upload = await async_client.post(
            "/upload",
            headers={"x-api-key": auth_headers["x-api-key"]},
            files=files,
            data={"entity_id": unique_entity_id},
        )
        upload_data = upload.json()
        session_id = upload_data["session_id"]
        file_id = upload_data["files"][0]["fileId"]
        filename = upload_data["files"][0]["filename"]

        # Execute: read input, transform, write output
        exec_response = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "code": (
                    "import csv\n"
                    f"with open('/mnt/data/{filename}') as f:\n"
                    "    reader = csv.DictReader(f)\n"
                    "    rows = list(reader)\n"
                    "with open('/mnt/data/output.csv', 'w', newline='') as f:\n"
                    "    writer = csv.DictWriter(f, fieldnames=['product', 'price', 'tax'])\n"
                    "    writer.writeheader()\n"
                    "    for row in rows:\n"
                    "        row['tax'] = f\"{float(row['price']) * 0.1:.2f}\"\n"
                    "        writer.writerow(row)\n"
                    "print('done')\n"
                ),
                "lang": "py",
                "session_id": session_id,
                "files": [
                    {"id": file_id, "session_id": session_id, "name": filename}
                ],
            },
        )

        result = exec_response.json()
        assert "done" in result["stdout"]
        assert len(result["files"]) >= 1

        # Find the generated output file
        output_file = next(
            (f for f in result["files"] if f["name"] == "output.csv"), None
        )
        assert output_file is not None, f"output.csv not in files: {result['files']}"

        # Download and verify content
        download = await async_client.get(
            f"/download/{session_id}/{output_file['id']}",
            headers=auth_headers,
        )
        assert download.status_code == 200
        downloaded_text = download.content.decode()
        assert "product,price,tax" in downloaded_text
        assert "Widget" in downloaded_text
        assert "1.00" in downloaded_text  # 9.99 * 0.1 = 1.00
