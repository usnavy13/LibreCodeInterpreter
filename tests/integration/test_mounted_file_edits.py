"""Integration tests for mounted file edit persistence.

These tests verify that in-place edits to mounted files are correctly
persisted after execution completes.

Note: Files uploaded WITH entity_id are "agent files" and are READ-ONLY.
Files uploaded WITHOUT entity_id are "user files" and can be edited.
"""

import pytest
import aiohttp
import ssl
import os
import time

# Test configuration - supports both BASE_URL and TEST_API_URL for flexibility
API_URL = os.getenv("BASE_URL") or os.getenv("TEST_API_URL", "https://localhost")
API_KEY = os.getenv("API_KEY") or os.getenv(
    "TEST_API_KEY", "test-api-key-for-development-only"
)


@pytest.fixture
def ssl_context():
    """Create SSL context that doesn't verify certificates for local testing."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


@pytest.fixture
def headers():
    """API headers."""
    return {"X-API-Key": API_KEY, "Content-Type": "application/json"}


@pytest.fixture
def upload_headers():
    """Headers for upload requests (no Content-Type for multipart)."""
    return {"X-API-Key": API_KEY}


class TestMountedFileEdits:
    """Test that edits to mounted files persist after execution."""

    @pytest.mark.asyncio
    async def test_edit_mounted_file_persists(
        self, ssl_context, headers, upload_headers
    ):
        """Test that editing a mounted file in-place persists the changes.

        1. Upload a file with content "original" (WITHOUT entity_id = user file)
        2. Execute code that modifies the file to "modified"
        3. Download the file
        4. Assert content is "modified"
        """
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            # Step 1: Upload a file with original content (NO entity_id = user file, editable)
            original_content = "original content"
            form_data = aiohttp.FormData()
            form_data.add_field(
                "files",
                original_content.encode(),
                filename="test.txt",
                content_type="text/plain",
            )
            # NOTE: No entity_id - this is a user file that can be edited

            async with session.post(
                f"{API_URL}/upload",
                data=form_data,
                headers=upload_headers,
                ssl=ssl_context,
            ) as resp:
                assert resp.status == 200, f"Upload failed: {await resp.text()}"
                upload_result = await resp.json()

                session_id = upload_result.get("session_id")
                uploaded_files = upload_result.get("files", [])
                assert len(uploaded_files) >= 1, "No files in upload response"

                uploaded_file = uploaded_files[0]
                file_id = uploaded_file.get("id") or uploaded_file.get("fileId")
                assert file_id is not None, "No file ID returned"

            # Step 2: Execute code that modifies the file in-place
            exec_payload = {
                "lang": "py",
                "code": """
with open('/mnt/data/test.txt', 'w') as f:
    f.write('modified content')
print('File modified')
""",
                "files": [
                    {"id": file_id, "session_id": session_id, "name": "test.txt"}
                ],
            }

            async with session.post(
                f"{API_URL}/exec", json=exec_payload, headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200, f"Exec failed: {await resp.text()}"
                exec_result = await resp.json()
                assert "File modified" in exec_result.get("stdout", "")

            # Step 3: Download the original file and verify content changed
            download_url = f"{API_URL}/download/{session_id}/{file_id}"
            async with session.get(
                download_url, headers=upload_headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200, f"Download failed: {resp.status}"
                content = await resp.text()

                # Step 4: Assert content is "modified"
                assert (
                    content == "modified content"
                ), f"Expected 'modified content', got '{content}'"

    @pytest.mark.asyncio
    async def test_edit_mounted_file_append(self, ssl_context, headers, upload_headers):
        """Test that appending to a mounted file persists."""
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            # Upload a file with initial content (NO entity_id = user file, editable)
            form_data = aiohttp.FormData()
            form_data.add_field(
                "files",
                b"line1\n",
                filename="log.txt",
                content_type="text/plain",
            )
            # NOTE: No entity_id - this is a user file that can be edited

            async with session.post(
                f"{API_URL}/upload",
                data=form_data,
                headers=upload_headers,
                ssl=ssl_context,
            ) as resp:
                assert resp.status == 200
                upload_result = await resp.json()
                session_id = upload_result.get("session_id")
                file_id = upload_result.get("files", [])[0].get(
                    "id"
                ) or upload_result.get("files", [])[0].get("fileId")

            # Append to the file
            exec_payload = {
                "lang": "py",
                "code": """
with open('/mnt/data/log.txt', 'a') as f:
    f.write('line2\\n')
    f.write('line3\\n')
print('Appended')
""",
                "files": [{"id": file_id, "session_id": session_id, "name": "log.txt"}],
            }

            async with session.post(
                f"{API_URL}/exec", json=exec_payload, headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200

            # Verify the appended content
            download_url = f"{API_URL}/download/{session_id}/{file_id}"
            async with session.get(
                download_url, headers=upload_headers, ssl=ssl_context
            ) as resp:
                content = await resp.text()
                assert "line1" in content
                assert "line2" in content
                assert "line3" in content

    @pytest.mark.asyncio
    async def test_delete_mounted_file_no_error(
        self, ssl_context, headers, upload_headers
    ):
        """Test that deleting a mounted file during execution doesn't cause errors."""
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            entity_id = f"test-delete-file-{int(time.time())}"

            # Upload a file
            form_data = aiohttp.FormData()
            form_data.add_field(
                "files",
                b"temporary content",
                filename="temp.txt",
                content_type="text/plain",
            )
            form_data.add_field("entity_id", entity_id)

            async with session.post(
                f"{API_URL}/upload",
                data=form_data,
                headers=upload_headers,
                ssl=ssl_context,
            ) as resp:
                assert resp.status == 200
                upload_result = await resp.json()
                session_id = upload_result.get("session_id")
                file_id = upload_result.get("files", [])[0].get(
                    "id"
                ) or upload_result.get("files", [])[0].get("fileId")

            # Delete the file during execution
            exec_payload = {
                "lang": "py",
                "code": """
import os
os.remove('/mnt/data/temp.txt')
print('File deleted')
""",
                "entity_id": entity_id,
                "files": [
                    {"id": file_id, "session_id": session_id, "name": "temp.txt"}
                ],
            }

            # Execution should succeed without errors
            async with session.post(
                f"{API_URL}/exec", json=exec_payload, headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200, f"Exec failed: {await resp.text()}"
                exec_result = await resp.json()
                assert "File deleted" in exec_result.get("stdout", "")
                # Should not have errors in stderr related to file update
                stderr = exec_result.get("stderr", "")
                assert "Failed to update mounted file" not in stderr

    @pytest.mark.asyncio
    async def test_edit_csv_file_persists(self, ssl_context, headers, upload_headers):
        """Test that editing a CSV file with pandas persists."""
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            # Upload a CSV file (NO entity_id = user file, editable)
            csv_content = "name,value\nAlice,10\nBob,20"
            form_data = aiohttp.FormData()
            form_data.add_field(
                "files",
                csv_content.encode(),
                filename="data.csv",
                content_type="text/csv",
            )
            # NOTE: No entity_id - this is a user file that can be edited

            async with session.post(
                f"{API_URL}/upload",
                data=form_data,
                headers=upload_headers,
                ssl=ssl_context,
            ) as resp:
                assert resp.status == 200
                upload_result = await resp.json()
                session_id = upload_result.get("session_id")
                file_id = upload_result.get("files", [])[0].get(
                    "id"
                ) or upload_result.get("files", [])[0].get("fileId")

            # Modify the CSV using pandas
            exec_payload = {
                "lang": "py",
                "code": """
import pandas as pd

df = pd.read_csv('/mnt/data/data.csv')
df['value'] = df['value'] * 2  # Double all values
df.to_csv('/mnt/data/data.csv', index=False)
print(f'Updated {len(df)} rows')
""",
                "files": [
                    {"id": file_id, "session_id": session_id, "name": "data.csv"}
                ],
            }

            async with session.post(
                f"{API_URL}/exec", json=exec_payload, headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200
                exec_result = await resp.json()
                assert "Updated 2 rows" in exec_result.get("stdout", "")

            # Download and verify the doubled values
            download_url = f"{API_URL}/download/{session_id}/{file_id}"
            async with session.get(
                download_url, headers=upload_headers, ssl=ssl_context
            ) as resp:
                content = await resp.text()
                # Original values were 10 and 20, should now be 20 and 40
                assert "20" in content
                assert "40" in content

    @pytest.mark.asyncio
    async def test_multiple_mounted_files_edited(
        self, ssl_context, headers, upload_headers
    ):
        """Test that multiple mounted files can be edited in one execution.

        NOTE: Files must be in the same session for both to be editable.
        Cross-session files are protected from modification.
        """
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            # Upload both files in a single upload (same session, NO entity_id = user files)
            form_data = aiohttp.FormData()
            form_data.add_field(
                "files",
                b"file1 original",
                filename="file1.txt",
                content_type="text/plain",
            )
            form_data.add_field(
                "files",
                b"file2 original",
                filename="file2.txt",
                content_type="text/plain",
            )
            # NOTE: No entity_id - these are user files that can be edited

            async with session.post(
                f"{API_URL}/upload",
                data=form_data,
                headers=upload_headers,
                ssl=ssl_context,
            ) as resp:
                result = await resp.json()
                session_id = result.get("session_id")
                files = result.get("files", [])
                file1_id = files[0].get("id") or files[0].get("fileId")
                file2_id = files[1].get("id") or files[1].get("fileId")

            # Edit both files
            exec_payload = {
                "lang": "py",
                "code": """
with open('/mnt/data/file1.txt', 'w') as f:
    f.write('file1 modified')
with open('/mnt/data/file2.txt', 'w') as f:
    f.write('file2 modified')
print('Both files modified')
""",
                "files": [
                    {"id": file1_id, "session_id": session_id, "name": "file1.txt"},
                    {"id": file2_id, "session_id": session_id, "name": "file2.txt"},
                ],
            }

            async with session.post(
                f"{API_URL}/exec", json=exec_payload, headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200

            # Verify both files were updated
            for file_id, expected in [
                (file1_id, "file1 modified"),
                (file2_id, "file2 modified"),
            ]:
                download_url = f"{API_URL}/download/{session_id}/{file_id}"
                async with session.get(
                    download_url, headers=upload_headers, ssl=ssl_context
                ) as resp:
                    content = await resp.text()
                    assert (
                        content == expected
                    ), f"Expected '{expected}', got '{content}'"

    @pytest.mark.asyncio
    async def test_edit_and_generate_files(self, ssl_context, headers, upload_headers):
        """Test that editing mounted files works alongside generating new files."""
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            # Upload a file (NO entity_id = user file, editable)
            form_data = aiohttp.FormData()
            form_data.add_field(
                "files",
                b"source data",
                filename="source.txt",
                content_type="text/plain",
            )
            # NOTE: No entity_id - this is a user file that can be edited

            async with session.post(
                f"{API_URL}/upload",
                data=form_data,
                headers=upload_headers,
                ssl=ssl_context,
            ) as resp:
                upload_result = await resp.json()
                session_id = upload_result.get("session_id")
                file_id = upload_result.get("files", [])[0].get(
                    "id"
                ) or upload_result.get("files", [])[0].get("fileId")

            # Edit the source file and generate a new output file
            exec_payload = {
                "lang": "py",
                "code": """
# Read and modify source
with open('/mnt/data/source.txt', 'r') as f:
    content = f.read()

# Overwrite source with processed content
with open('/mnt/data/source.txt', 'w') as f:
    f.write(content.upper())

# Generate a new output file
with open('/mnt/data/output.txt', 'w') as f:
    f.write(f'Processed: {content.upper()}')

print('Done')
""",
                "files": [
                    {"id": file_id, "session_id": session_id, "name": "source.txt"}
                ],
            }

            async with session.post(
                f"{API_URL}/exec", json=exec_payload, headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200
                exec_result = await resp.json()

                # Should have generated a new file
                files = exec_result.get("files", [])
                output_file = next(
                    (f for f in files if f.get("name") == "output.txt"), None
                )
                assert output_file is not None, "output.txt not in generated files"

            # Verify source file was modified
            download_url = f"{API_URL}/download/{session_id}/{file_id}"
            async with session.get(
                download_url, headers=upload_headers, ssl=ssl_context
            ) as resp:
                content = await resp.text()
                assert (
                    content == "SOURCE DATA"
                ), f"Expected 'SOURCE DATA', got '{content}'"

            # Verify output file was created
            exec_session_id = exec_result.get("session_id")
            output_download_url = (
                f"{API_URL}/download/{exec_session_id}/{output_file['id']}"
            )
            async with session.get(
                output_download_url, headers=upload_headers, ssl=ssl_context
            ) as resp:
                content = await resp.text()
                assert "Processed: SOURCE DATA" in content


class TestAgentFileReadOnlyProtection:
    """Test that agent-assigned files (uploaded with entity_id) are read-only."""

    @pytest.mark.asyncio
    async def test_agent_file_not_modified(self, ssl_context, headers, upload_headers):
        """Test that files uploaded with entity_id cannot be modified.

        Agent files are read-only to prevent users from corrupting
        data that the agent creator assigned.
        """
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            entity_id = f"test-agent-readonly-{int(time.time())}"

            # Upload a file WITH entity_id (agent file = read-only)
            original_content = "agent data - do not modify"
            form_data = aiohttp.FormData()
            form_data.add_field(
                "files",
                original_content.encode(),
                filename="agent_data.txt",
                content_type="text/plain",
            )
            form_data.add_field("entity_id", entity_id)

            async with session.post(
                f"{API_URL}/upload",
                data=form_data,
                headers=upload_headers,
                ssl=ssl_context,
            ) as resp:
                assert resp.status == 200
                upload_result = await resp.json()
                session_id = upload_result.get("session_id")
                file_id = upload_result.get("files", [])[0].get(
                    "id"
                ) or upload_result.get("files", [])[0].get("fileId")

            # Try to modify the agent file
            exec_payload = {
                "lang": "py",
                "code": """
with open('/mnt/data/agent_data.txt', 'w') as f:
    f.write('HACKED BY USER')
print('Attempted modification')
""",
                "entity_id": entity_id,
                "files": [
                    {"id": file_id, "session_id": session_id, "name": "agent_data.txt"}
                ],
            }

            async with session.post(
                f"{API_URL}/exec", json=exec_payload, headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200
                exec_result = await resp.json()
                # Code executes successfully (file is modified in container)
                assert "Attempted modification" in exec_result.get("stdout", "")

            # Download the file - should still have original content
            download_url = f"{API_URL}/download/{session_id}/{file_id}"
            async with session.get(
                download_url, headers=upload_headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200
                content = await resp.text()
                # Agent file should NOT be modified
                assert (
                    content == original_content
                ), f"Agent file was modified! Expected '{original_content}', got '{content}'"

    @pytest.mark.asyncio
    async def test_user_file_can_be_modified(
        self, ssl_context, headers, upload_headers
    ):
        """Test that files uploaded WITHOUT entity_id CAN be modified.

        User files should be editable (this is the counterpart to the above test).
        """
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            # Upload a file WITHOUT entity_id (user file = editable)
            original_content = "user data"
            form_data = aiohttp.FormData()
            form_data.add_field(
                "files",
                original_content.encode(),
                filename="user_data.txt",
                content_type="text/plain",
            )
            # NOTE: No entity_id - this is a user file

            async with session.post(
                f"{API_URL}/upload",
                data=form_data,
                headers=upload_headers,
                ssl=ssl_context,
            ) as resp:
                assert resp.status == 200
                upload_result = await resp.json()
                session_id = upload_result.get("session_id")
                file_id = upload_result.get("files", [])[0].get(
                    "id"
                ) or upload_result.get("files", [])[0].get("fileId")

            # Modify the user file
            exec_payload = {
                "lang": "py",
                "code": """
with open('/mnt/data/user_data.txt', 'w') as f:
    f.write('MODIFIED BY USER')
print('Modified user file')
""",
                "files": [
                    {"id": file_id, "session_id": session_id, "name": "user_data.txt"}
                ],
            }

            async with session.post(
                f"{API_URL}/exec", json=exec_payload, headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200

            # Download the file - should have modified content
            download_url = f"{API_URL}/download/{session_id}/{file_id}"
            async with session.get(
                download_url, headers=upload_headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200
                content = await resp.text()
                # User file SHOULD be modified
                assert (
                    content == "MODIFIED BY USER"
                ), f"User file was not modified! Expected 'MODIFIED BY USER', got '{content}'"
