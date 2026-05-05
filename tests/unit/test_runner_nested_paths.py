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
        # _detect_generated_files reads this to skip unchanged mounted files
        # and surface in-place edits. Empty for tests that don't exercise mounts.
        mounted_file_stats={},
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

    async def test_skips_node_modules(self, runner, tmp_path):
        """A user file at the top level should be detected; the entire
        node_modules tree (which can contain tens of thousands of files
        from one `npm install`) should be ignored entirely."""
        info = _sandbox_info(tmp_path)
        (info.data_dir / "user_output.png").write_bytes(b"x")
        nm = info.data_dir / "node_modules"
        nm.mkdir()
        (nm / "package1").mkdir()
        (nm / "package1" / "index.js").write_bytes(b"// pkg")
        (nm / "package1" / "README.md").write_bytes(b"# readme")
        (nm / "package2").mkdir()
        (nm / "package2" / "index.js").write_bytes(b"// pkg2")

        files = await runner._detect_generated_files(info)
        paths = [f["path"] for f in files]

        assert "/mnt/data/user_output.png" in paths
        assert all("node_modules" not in p for p in paths), paths

    async def test_skips_pycache_and_other_dep_dirs(self, runner, tmp_path):
        info = _sandbox_info(tmp_path)
        (info.data_dir / "report.csv").write_bytes(b"data")
        for skip in ("__pycache__", ".venv", "target", "dist", "build"):
            d = info.data_dir / skip
            d.mkdir()
            (d / "junk.bin").write_bytes(b"x" * 100)

        files = await runner._detect_generated_files(info)
        paths = [f["path"] for f in files]

        assert paths == ["/mnt/data/report.csv"]

    async def test_includes_user_subdirs_that_arent_dep_caches(self, runner, tmp_path):
        """Don't over-exclude — `charts/`, `data/`, etc. are user content."""
        info = _sandbox_info(tmp_path)
        (info.data_dir / "charts").mkdir()
        (info.data_dir / "charts" / "out.png").write_bytes(b"png")
        (info.data_dir / "data").mkdir()
        (info.data_dir / "data" / "rows.csv").write_bytes(b"csv")

        files = await runner._detect_generated_files(info)
        paths = sorted(f["path"] for f in files)

        assert paths == [
            "/mnt/data/charts/out.png",
            "/mnt/data/data/rows.csv",
        ]


class TestDetectGeneratedFilesInPlaceEdits:
    """The mtime/size snapshot stored in `sandbox_info.mounted_file_stats`
    drives whether a mounted file gets surfaced as a generated file. This
    is the iteration-killer fix: edits to mounted scripts must produce a
    new file_id so LibreChat tracks the edit on its next call."""

    async def test_unchanged_mounted_file_is_skipped(self, runner, tmp_path):
        info = _sandbox_info(tmp_path)
        f = info.data_dir / "demo_deck.js"
        f.write_bytes(b"// v1 content\n")
        st = os.stat(f)
        info.mounted_file_stats = {"demo_deck.js": (st.st_mtime_ns, st.st_size)}

        files = await runner._detect_generated_files(info)
        paths = [f["path"] for f in files]

        # No edit happened -> the mounted file is not surfaced.
        assert paths == []

    async def test_edited_mounted_file_is_surfaced(self, runner, tmp_path):
        info = _sandbox_info(tmp_path)
        f = info.data_dir / "demo_deck.js"
        f.write_bytes(b"// v1 content\n")
        st = os.stat(f)
        info.mounted_file_stats = {"demo_deck.js": (st.st_mtime_ns, st.st_size)}

        # Simulate user code editing the file in place. Touching mtime is
        # enough since size also changes here, but we'd want to detect either.
        import time

        time.sleep(0.01)  # ensure mtime_ns advances on coarse-grained FS
        f.write_bytes(b"// v2 content with extra bytes\n")

        files = await runner._detect_generated_files(info)
        paths = [f["path"] for f in files]

        # Edited mounted file is now surfaced as a generated file.
        # Orchestrator will create a new file_id for it.
        assert paths == ["/mnt/data/demo_deck.js"]

    async def test_size_change_is_detected_even_if_mtime_unchanged(
        self, runner, tmp_path
    ):
        """Defensive: if mtime is somehow preserved but size differs,
        treat as edited."""
        info = _sandbox_info(tmp_path)
        f = info.data_dir / "report.csv"
        f.write_bytes(b"col1\n")
        st = os.stat(f)
        # Pretend the prior snapshot had a different size at the same mtime.
        info.mounted_file_stats = {"report.csv": (st.st_mtime_ns, st.st_size + 100)}

        files = await runner._detect_generated_files(info)
        paths = [f["path"] for f in files]

        assert paths == ["/mnt/data/report.csv"]

    async def test_nested_mounted_file_edit_is_surfaced(self, runner, tmp_path):
        """Mounted file at a nested path (e.g. skills/foo/SKILL.md) — edit
        detection must work whether the snapshot key is the rel path or the
        basename."""
        info = _sandbox_info(tmp_path)
        sub = info.data_dir / "skills" / "weather"
        sub.mkdir(parents=True)
        f = sub / "SKILL.md"
        f.write_bytes(b"# v1\n")
        st = os.stat(f)
        info.mounted_file_stats = {
            "skills/weather/SKILL.md": (st.st_mtime_ns, st.st_size),
            "SKILL.md": (st.st_mtime_ns, st.st_size),
        }

        # No change: skipped.
        assert await runner._detect_generated_files(info) == []

        # Edit: surfaced.
        import time

        time.sleep(0.01)
        f.write_bytes(b"# v2 content edited\n")
        files = await runner._detect_generated_files(info)
        paths = [f["path"] for f in files]
        assert paths == ["/mnt/data/skills/weather/SKILL.md"]

    async def test_new_file_alongside_unchanged_mount(self, runner, tmp_path):
        """A truly-new file is detected even when an unchanged mount sits
        next to it."""
        info = _sandbox_info(tmp_path)
        existing = info.data_dir / "input.csv"
        existing.write_bytes(b"data")
        st = os.stat(existing)
        info.mounted_file_stats = {"input.csv": (st.st_mtime_ns, st.st_size)}

        # User code generates a new artifact.
        (info.data_dir / "output.png").write_bytes(b"png")

        files = await runner._detect_generated_files(info)
        paths = sorted(f["path"] for f in files)

        # Mounted file unchanged (skipped); new file surfaced.
        assert paths == ["/mnt/data/output.png"]


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
