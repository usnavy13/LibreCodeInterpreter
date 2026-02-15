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
