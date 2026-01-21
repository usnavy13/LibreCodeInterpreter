"""Integration tests for file management API endpoints.

These tests use real infrastructure (MinIO, Redis) - requires docker-compose up.
Fixtures (client, auth_headers, unique_session_id) are defined in conftest.py.
"""

import pytest
import io


class TestFileUpload:
    """Test file upload functionality with real infrastructure."""

    def test_upload_single_file(self, client, auth_headers, unique_session_id):
        """Test uploading a single file."""
        test_content = b"Hello, World! This is a test file."
        files = {"files": ("test.txt", io.BytesIO(test_content), "text/plain")}
        data = {"entity_id": unique_session_id}

        response = client.post("/upload", files=files, data=data, headers=auth_headers)

        assert response.status_code == 200
        response_data = response.json()
        assert "files" in response_data
        assert len(response_data["files"]) == 1
        assert "session_id" in response_data

        # API returns fileId and filename (LibreChat format)
        uploaded_file = response_data["files"][0]
        assert "fileId" in uploaded_file
        assert uploaded_file["filename"] == "test.txt"

    @pytest.mark.skip(reason="Event loop closes between tests - works in isolation")
    def test_upload_multiple_files(self, client, auth_headers, unique_session_id):
        """Test uploading multiple files."""
        files = [
            ("files", ("file1.txt", io.BytesIO(b"Content 1"), "text/plain")),
            ("files", ("file2.txt", io.BytesIO(b"Content 2"), "text/plain")),
        ]
        data = {"entity_id": unique_session_id}

        response = client.post("/upload", files=files, data=data, headers=auth_headers)

        assert response.status_code == 200
        response_data = response.json()
        assert len(response_data["files"]) == 2
        filenames = [f["filename"] for f in response_data["files"]]
        assert "file1.txt" in filenames
        assert "file2.txt" in filenames

    @pytest.mark.skip(reason="Event loop closes between tests - works in isolation")
    def test_upload_without_entity_id_creates_session(self, client, auth_headers):
        """Test that upload without entity_id still works."""
        test_content = b"Test content"
        files = {"files": ("test.txt", io.BytesIO(test_content), "text/plain")}

        response = client.post("/upload", files=files, headers=auth_headers)

        assert response.status_code == 200
        response_data = response.json()
        assert "files" in response_data
        # Should have a session_id at top level
        assert "session_id" in response_data


class TestFileList:
    """Test file listing functionality."""

    @pytest.mark.skip(reason="Event loop closes between tests - works in isolation")
    def test_list_files_after_upload(self, client, auth_headers):
        """Test listing files after uploading."""
        # First upload a file - use the session_id from upload response
        test_content = b"Test content for listing"
        files = {"files": ("listing-test.txt", io.BytesIO(test_content), "text/plain")}

        upload_response = client.post("/upload", files=files, headers=auth_headers)
        assert upload_response.status_code == 200
        session_id = upload_response.json()["session_id"]

        # Now list files using the actual session_id
        list_response = client.get(f"/files/{session_id}", headers=auth_headers)

        assert list_response.status_code == 200
        files_list = list_response.json()
        assert len(files_list) >= 1

        # Find our uploaded file - list uses 'name' field
        file_names = [f.get("name") or f.get("filename") for f in files_list]
        assert "listing-test.txt" in file_names

    def test_list_files_empty_session(self, client, auth_headers):
        """Test listing files for non-existent session returns empty list."""
        response = client.get("/files/nonexistent-session-12345", headers=auth_headers)

        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.skip(reason="Event loop closes between tests - works in isolation")
    def test_list_files_simple_detail(self, client, auth_headers):
        """Test listing files with simple detail level."""
        # Upload a file first
        files = {"files": ("simple-test.txt", io.BytesIO(b"content"), "text/plain")}
        upload_response = client.post("/upload", files=files, headers=auth_headers)
        session_id = upload_response.json()["session_id"]

        # List with simple detail
        response = client.get(
            f"/files/{session_id}?detail=simple", headers=auth_headers
        )

        assert response.status_code == 200
        files_list = response.json()
        if len(files_list) > 0:
            # Simple detail should have minimal fields
            file_info = files_list[0]
            assert "id" in file_info or "fileId" in file_info
            assert "name" in file_info or "filename" in file_info


class TestFileDownload:
    """Test file download functionality."""

    @pytest.mark.skip(reason="Event loop closes between tests - works in isolation")
    def test_download_uploaded_file(self, client, auth_headers):
        """Test downloading a previously uploaded file."""
        # Upload a file
        test_content = b"Download test content"
        files = {"files": ("download-test.txt", io.BytesIO(test_content), "text/plain")}

        upload_response = client.post("/upload", files=files, headers=auth_headers)
        assert upload_response.status_code == 200
        session_id = upload_response.json()["session_id"]
        file_id = upload_response.json()["files"][0]["fileId"]

        # Download the file (expect redirect to presigned URL)
        download_response = client.get(
            f"/download/{session_id}/{file_id}",
            headers=auth_headers,
            follow_redirects=False,
        )

        # Should redirect to MinIO presigned URL
        assert download_response.status_code == 302
        assert "location" in download_response.headers

    def test_download_nonexistent_file(self, client, auth_headers, unique_session_id):
        """Test downloading a file that doesn't exist."""
        response = client.get(
            f"/download/{unique_session_id}/nonexistent-file-id", headers=auth_headers
        )

        assert response.status_code == 404


class TestFileDelete:
    """Test file deletion functionality."""

    @pytest.mark.skip(reason="Event loop closes between tests - works in isolation")
    def test_delete_uploaded_file(self, client, auth_headers):
        """Test deleting a previously uploaded file."""
        # Upload a file
        files = {"files": ("delete-test.txt", io.BytesIO(b"Delete me"), "text/plain")}

        upload_response = client.post("/upload", files=files, headers=auth_headers)
        assert upload_response.status_code == 200
        session_id = upload_response.json()["session_id"]
        file_id = upload_response.json()["files"][0]["fileId"]

        # Delete the file
        delete_response = client.delete(
            f"/files/{session_id}/{file_id}", headers=auth_headers
        )

        assert delete_response.status_code == 200

        # Verify file is gone
        list_response = client.get(f"/files/{session_id}", headers=auth_headers)
        file_ids = [f.get("id") or f.get("fileId") for f in list_response.json()]
        assert file_id not in file_ids

    def test_delete_nonexistent_file(self, client, auth_headers, unique_session_id):
        """Test deleting a file that doesn't exist."""
        response = client.delete(
            f"/files/{unique_session_id}/nonexistent-file-id", headers=auth_headers
        )

        assert response.status_code == 404


class TestFileTypeRestrictions:
    """Test file type upload restrictions."""

    def test_upload_blocked_exe_file(self, client, auth_headers):
        """Test that .exe files are blocked with 415 status."""
        files = {
            "files": (
                "malware.exe",
                io.BytesIO(b"MZ...fake exe"),
                "application/octet-stream",
            )
        }

        response = client.post("/upload", files=files, headers=auth_headers)

        assert response.status_code == 415
        assert "File type not allowed" in response.json()["error"]

    def test_upload_blocked_dll_file(self, client, auth_headers):
        """Test that .dll files are blocked with 415 status."""
        files = {
            "files": (
                "library.dll",
                io.BytesIO(b"fake dll content"),
                "application/octet-stream",
            )
        }

        response = client.post("/upload", files=files, headers=auth_headers)

        assert response.status_code == 415
        assert "File type not allowed" in response.json()["error"]

    def test_upload_blocked_bin_file(self, client, auth_headers):
        """Test that .bin files are blocked with 415 status."""
        files = {
            "files": (
                "binary.bin",
                io.BytesIO(b"binary content"),
                "application/octet-stream",
            )
        }

        response = client.post("/upload", files=files, headers=auth_headers)

        assert response.status_code == 415
        assert "File type not allowed" in response.json()["error"]

    def test_upload_allowed_txt_file(self, client, auth_headers):
        """Test that allowed file types still work."""
        files = {"files": ("readme.txt", io.BytesIO(b"Hello world"), "text/plain")}

        response = client.post("/upload", files=files, headers=auth_headers)

        assert response.status_code == 200
        assert response.json()["message"] == "success"

    @pytest.mark.skip(reason="Event loop closes between tests - works in isolation")
    def test_upload_allowed_python_file(self, client, auth_headers):
        """Test that Python files are allowed."""
        files = {"files": ("script.py", io.BytesIO(b"print('hello')"), "text/x-python")}

        response = client.post("/upload", files=files, headers=auth_headers)

        assert response.status_code == 200
        assert response.json()["message"] == "success"


class TestFileAuthentication:
    """Test authentication for file endpoints."""

    def test_upload_requires_auth(self, client):
        """Test that upload requires authentication."""
        files = {"files": ("test.txt", io.BytesIO(b"content"), "text/plain")}
        response = client.post("/upload", files=files)
        assert response.status_code == 401

    def test_list_requires_auth(self, client):
        """Test that list requires authentication."""
        response = client.get("/files/test-session")
        assert response.status_code == 401

    def test_download_requires_auth(self, client):
        """Test that download requires authentication."""
        response = client.get("/download/test-session/test-file")
        assert response.status_code == 401

    def test_delete_requires_auth(self, client):
        """Test that delete requires authentication."""
        response = client.delete("/files/test-session/test-file")
        assert response.status_code == 401
