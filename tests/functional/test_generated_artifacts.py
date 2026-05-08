"""Functional tests for generated file artifacts against a live API."""

import pytest


class TestGeneratedArtifacts:
    """Verify generated artifacts are complete and reusable."""

    @pytest.mark.asyncio
    async def test_generated_image_download_is_valid_png(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Generated PNG downloads should contain real binary image data."""
        response = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "lang": "py",
                "code": (
                    "import matplotlib.pyplot as plt\n"
                    "plt.figure(figsize=(6, 4))\n"
                    "plt.plot([1, 2, 3, 4], [1, 4, 9, 16], 'ro-')\n"
                    "plt.title('Test Chart')\n"
                    "plt.savefig('/mnt/data/test_chart.png', dpi=100)\n"
                    "print('Chart saved')\n"
                ),
                "entity_id": unique_entity_id,
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()

        assert result["files"], "Expected at least one generated file"
        file_info = result["files"][0]
        assert file_info["name"] == "test_chart.png"

        download = await async_client.get(
            f"/download/{result['session_id']}/{file_info['id']}",
            headers=auth_headers,
        )
        assert download.status_code == 200
        assert len(download.content) > 1000
        assert download.content[:8] == b"\x89PNG\r\n\x1a\n"

    @pytest.mark.asyncio
    async def test_multiple_generated_files_are_all_downloadable(
        self, async_client, auth_headers, unique_entity_id
    ):
        """Multiple generated artifacts should all be persisted and downloadable."""
        response = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "lang": "py",
                "code": (
                    "import matplotlib.pyplot as plt\n"
                    "for name in ['alpha', 'beta', 'gamma']:\n"
                    "    plt.figure()\n"
                    "    plt.plot([1, 2, 3], [1, 4, 9])\n"
                    "    plt.title(f'{name} plot')\n"
                    "    plt.savefig(f'/mnt/data/{name}.png')\n"
                    "    print(f'Created {name}.png')\n"
                ),
                "entity_id": unique_entity_id,
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()

        files = result["files"]
        assert len(files) >= 3, f"Expected 3 files, got {len(files)}"
        assert {"alpha.png", "beta.png", "gamma.png"} <= {
            file_info["name"] for file_info in files
        }

        for file_info in files:
            download = await async_client.get(
                f"/download/{result['session_id']}/{file_info['id']}",
                headers=auth_headers,
            )
            assert download.status_code == 200
            assert len(download.content) > 1000
            assert download.content[:4] == b"\x89PNG"

    @pytest.mark.asyncio
    async def test_generated_file_is_reused_on_follow_up_execution(
        self, async_client, auth_headers, unique_entity_id
    ):
        """A generated artifact should be immediately reusable in the next exec."""
        generate = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "lang": "py",
                "code": (
                    "with open('/mnt/data/output.txt', 'w') as f:\n"
                    "    f.write('Hello from generated file')\n"
                    "print('generated')\n"
                ),
                "entity_id": unique_entity_id,
            },
        )
        assert generate.status_code == 200, generate.text
        generate_result = generate.json()
        generated_file = generate_result["files"][0]

        reuse = await async_client.post(
            "/exec",
            headers=auth_headers,
            json={
                "lang": "py",
                "code": "print(open('output.txt').read())",
                "session_id": generate_result["session_id"],
                "files": [
                    {
                        "id": generated_file["id"],
                        "storage_session_id": generate_result["session_id"],
                        "name": generated_file["name"],
                    }
                ],
            },
        )
        assert reuse.status_code == 200, reuse.text
        assert "Hello from generated file" in reuse.json()["stdout"]
