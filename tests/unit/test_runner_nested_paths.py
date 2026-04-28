"""Unit tests for ExecutionRunner nested-path handling.

Covers the two filesystem-touching points where subdirectory structure must
survive the round-trip:
  - `_detect_generated_files` (output side: scan /mnt/data recursively)
  - `_mount_files_to_sandbox` (input side: create parent dirs before writing)

Both are exercised against real temporary directories so we don't have to
mock the os.walk / mkdir / chown call graph.
"""

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.execution.runner import CodeExecutionRunner


@pytest.fixture
def runner():
    """Build a runner with the dependencies it needs for these methods stubbed."""
    return CodeExecutionRunner(
        sandbox_manager=MagicMock(),
        sandbox_pool=None,
    )


def _sandbox_info(tmp_path: Path):
    """Minimal SandboxInfo-shaped object with a real data_dir."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return SimpleNamespace(
        sandbox_id="test-sandbox-id",
        data_dir=data_dir,
        repl_mode=False,
    )


class TestDetectGeneratedFilesRecursive:
    async def test_walks_subdirectories(self, runner, tmp_path):
        info = _sandbox_info(tmp_path)
        # Top-level + one nested + two-deep nested
        (info.data_dir / "top.png").write_bytes(b"x")
        (info.data_dir / "charts").mkdir()
        (info.data_dir / "charts" / "out.png").write_bytes(b"y")
        (info.data_dir / "charts" / "sub").mkdir()
        (info.data_dir / "charts" / "sub" / "deep.txt").write_bytes(b"z")

        files = await runner._detect_generated_files(info)

        names = sorted(f["path"] for f in files)
        assert names == [
            "/mnt/data/charts/out.png",
            "/mnt/data/charts/sub/deep.txt",
            "/mnt/data/top.png",
        ]

    async def test_skips_hidden_files_and_dirs(self, runner, tmp_path):
        info = _sandbox_info(tmp_path)
        (info.data_dir / "visible.txt").write_bytes(b"v")
        (info.data_dir / ".hidden_file").write_bytes(b"h")
        (info.data_dir / ".hidden_dir").mkdir()
        (info.data_dir / ".hidden_dir" / "inside.txt").write_bytes(b"i")

        files = await runner._detect_generated_files(info)

        paths = [f["path"] for f in files]
        assert "/mnt/data/visible.txt" in paths
        assert all(".hidden" not in p for p in paths)

    async def test_skips_code_source_files(self, runner, tmp_path):
        info = _sandbox_info(tmp_path)
        (info.data_dir / "code.py").write_bytes(b"# code")
        (info.data_dir / "Code.java").write_bytes(b"// Code")
        (info.data_dir / "actual_output.txt").write_bytes(b"out")

        files = await runner._detect_generated_files(info)

        paths = [f["path"] for f in files]
        assert paths == ["/mnt/data/actual_output.txt"]

    async def test_results_sorted_for_stability(self, runner, tmp_path):
        info = _sandbox_info(tmp_path)
        for name in ["zeta.txt", "alpha.txt", "mid.txt"]:
            (info.data_dir / name).write_bytes(b"x")

        files = await runner._detect_generated_files(info)

        paths = [f["path"] for f in files]
        assert paths == sorted(paths)

    async def test_oversized_files_excluded(self, runner, tmp_path):
        info = _sandbox_info(tmp_path)
        (info.data_dir / "small.txt").write_bytes(b"x")

        with patch("src.services.execution.runner.settings") as ms:
            ms.max_file_size_mb = 0  # cap to 0 bytes -> nothing fits
            ms.max_output_files = 50
            files = await runner._detect_generated_files(info)

        assert files == []

    async def test_max_output_files_applied_after_sort(self, runner, tmp_path):
        info = _sandbox_info(tmp_path)
        for name in ["c.txt", "a.txt", "b.txt", "d.txt"]:
            (info.data_dir / name).write_bytes(b"x")

        with patch("src.services.execution.runner.settings") as ms:
            ms.max_file_size_mb = 100
            ms.max_output_files = 2
            files = await runner._detect_generated_files(info)

        # First two after sorting alphabetically
        assert [f["path"] for f in files] == ["/mnt/data/a.txt", "/mnt/data/b.txt"]


class TestMountFilesNestedPaths:
    """The mount path is harder to fully exercise because it pulls bytes from
    MinIO. We patch FileService.stream_file_to_path and just confirm that
    parent directories are created at the right nested location."""

    async def test_nested_filename_creates_parent_dirs(self, runner, tmp_path):
        info = _sandbox_info(tmp_path)

        # Avoid os.chown (would need root); patch the perm helpers to no-op.
        async def _fake_stream(session_id, file_id, dest_path):
            Path(dest_path).write_bytes(b"hello")
            return True

        with patch("src.services.file.FileService") as MockFS, patch(
            "src.services.execution.runner.os.chown"
        ), patch("src.services.execution.runner.os.chmod"):
            instance = MockFS.return_value
            instance.stream_file_to_path = AsyncMock(side_effect=_fake_stream)

            files = [
                {
                    "filename": "skills/foo/SKILL.md",
                    "file_id": "fid-1",
                    "session_id": "sid-1",
                    "size": 10,
                }
            ]
            await runner._mount_files_to_sandbox(info, files, language="py")

        landed = info.data_dir / "skills" / "foo" / "SKILL.md"
        assert landed.is_file()
        assert landed.read_bytes() == b"hello"

    async def test_top_level_filename_unchanged(self, runner, tmp_path):
        info = _sandbox_info(tmp_path)

        async def _fake_stream(session_id, file_id, dest_path):
            Path(dest_path).write_bytes(b"data")
            return True

        with patch("src.services.file.FileService") as MockFS, patch(
            "src.services.execution.runner.os.chown"
        ), patch("src.services.execution.runner.os.chmod"):
            instance = MockFS.return_value
            instance.stream_file_to_path = AsyncMock(side_effect=_fake_stream)

            files = [
                {
                    "filename": "data.csv",
                    "file_id": "fid",
                    "session_id": "sid",
                    "size": 4,
                }
            ]
            await runner._mount_files_to_sandbox(info, files, language="py")

        landed = info.data_dir / "data.csv"
        assert landed.is_file()
