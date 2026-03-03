"""Unit tests for SandboxManager."""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from src.services.sandbox.manager import SandboxManager


class TestSandboxManagerAvailability:
    """Test SandboxManager availability checks."""

    def test_is_available_when_nsjail_exists(self):
        """Test is_available returns True when nsjail binary is found."""
        with patch("shutil.which", return_value="/usr/bin/nsjail"):
            with patch.object(SandboxManager, "__init__", lambda self: None):
                manager = SandboxManager()
                manager._nsjail_config = MagicMock()
                manager._executor = MagicMock()
                manager._base_dir = Path("/tmp/test-sandboxes")
                manager._initialization_error = None
                assert manager.is_available() is True

    def test_is_not_available_when_nsjail_missing(self):
        """Test is_available returns False when nsjail binary is not found."""
        with patch("shutil.which", return_value=None):
            with patch.object(SandboxManager, "__init__", lambda self: None):
                manager = SandboxManager()
                manager._nsjail_config = MagicMock()
                manager._executor = MagicMock()
                manager._base_dir = Path("/tmp/test-sandboxes")
                manager._initialization_error = None
                assert manager.is_available() is False

    def test_get_initialization_error_nsjail_missing(self):
        """Test error message when nsjail is not available."""
        with patch("shutil.which", return_value=None):
            with patch.object(SandboxManager, "__init__", lambda self: None):
                manager = SandboxManager()
                manager._nsjail_config = MagicMock()
                manager._executor = MagicMock()
                manager._base_dir = Path("/tmp/test-sandboxes")
                manager._initialization_error = None
                error = manager.get_initialization_error()
                assert error is not None
                assert "nsjail" in error.lower()

    def test_get_initialization_error_none_when_available(self):
        """Test no error when nsjail is available and init succeeded."""
        with patch("shutil.which", return_value="/usr/bin/nsjail"):
            with patch.object(SandboxManager, "__init__", lambda self: None):
                manager = SandboxManager()
                manager._nsjail_config = MagicMock()
                manager._executor = MagicMock()
                manager._base_dir = Path("/tmp/test-sandboxes")
                manager._initialization_error = None
                assert manager.get_initialization_error() is None

    def test_get_initialization_error_from_init(self):
        """Test initialization error is reported."""
        with patch("shutil.which", return_value="/usr/bin/nsjail"):
            with patch.object(SandboxManager, "__init__", lambda self: None):
                manager = SandboxManager()
                manager._nsjail_config = MagicMock()
                manager._executor = MagicMock()
                manager._base_dir = Path("/tmp/test-sandboxes")
                manager._initialization_error = "Failed to create directory"
                assert manager.get_initialization_error() == "Failed to create directory"


class TestSandboxLifecycle:
    """Test sandbox creation and destruction."""

    def test_create_sandbox_creates_directory(self, tmp_path):
        """Test create_sandbox creates the data directory."""
        with patch("shutil.which", return_value="/usr/bin/nsjail"), \
             patch("os.chown"):
            with patch.object(SandboxManager, "__init__", lambda self: None):
                manager = SandboxManager()
                manager._nsjail_config = MagicMock()
                manager._executor = MagicMock()
                manager._base_dir = tmp_path
                manager._initialization_error = None

                info = manager.create_sandbox("session1", "py")

                assert info.data_dir.exists()
                assert info.language == "py"
                assert info.session_id == "session1"

    def test_create_sandbox_sets_repl_mode(self, tmp_path):
        """Test create_sandbox sets repl_mode correctly."""
        with patch("shutil.which", return_value="/usr/bin/nsjail"), \
             patch("os.chown"):
            with patch.object(SandboxManager, "__init__", lambda self: None):
                manager = SandboxManager()
                manager._nsjail_config = MagicMock()
                manager._executor = MagicMock()
                manager._base_dir = tmp_path
                manager._initialization_error = None

                info = manager.create_sandbox("session1", "py", repl_mode=True)

                assert info.repl_mode is True

    def test_create_sandbox_sets_labels(self, tmp_path):
        """Test create_sandbox sets appropriate labels."""
        with patch("shutil.which", return_value="/usr/bin/nsjail"), \
             patch("os.chown"):
            with patch.object(SandboxManager, "__init__", lambda self: None):
                manager = SandboxManager()
                manager._nsjail_config = MagicMock()
                manager._executor = MagicMock()
                manager._base_dir = tmp_path
                manager._initialization_error = None

                info = manager.create_sandbox("session1", "py")

                assert info.labels["com.code-interpreter.managed"] == "true"
                assert info.labels["com.code-interpreter.session-id"] == "session1"
                assert info.labels["com.code-interpreter.language"] == "py"

    def test_create_sandbox_generates_unique_ids(self, tmp_path):
        """Test create_sandbox generates unique sandbox IDs."""
        with patch("shutil.which", return_value="/usr/bin/nsjail"), \
             patch("os.chown"):
            with patch.object(SandboxManager, "__init__", lambda self: None):
                manager = SandboxManager()
                manager._nsjail_config = MagicMock()
                manager._executor = MagicMock()
                manager._base_dir = tmp_path
                manager._initialization_error = None

                info1 = manager.create_sandbox("session1", "py")
                info2 = manager.create_sandbox("session2", "py")

                assert info1.sandbox_id != info2.sandbox_id

    def test_destroy_sandbox_removes_directory(self, tmp_path):
        """Test destroy_sandbox removes the sandbox directory."""
        with patch("shutil.which", return_value="/usr/bin/nsjail"), \
             patch("os.chown"):
            with patch.object(SandboxManager, "__init__", lambda self: None):
                manager = SandboxManager()
                manager._nsjail_config = MagicMock()
                manager._executor = MagicMock()
                manager._base_dir = tmp_path
                manager._initialization_error = None

                info = manager.create_sandbox("session1", "py")
                assert info.sandbox_dir.exists()

                result = manager.destroy_sandbox(info)
                assert result is True
                assert not info.sandbox_dir.exists()

    def test_destroy_sandbox_nonexistent_returns_true(self, tmp_path):
        """Test destroying a non-existent sandbox returns True."""
        with patch("shutil.which", return_value="/usr/bin/nsjail"), \
             patch("os.chown"):
            with patch.object(SandboxManager, "__init__", lambda self: None):
                manager = SandboxManager()
                manager._nsjail_config = MagicMock()
                manager._executor = MagicMock()
                manager._base_dir = tmp_path
                manager._initialization_error = None

                from src.services.sandbox.nsjail import SandboxInfo
                from datetime import datetime

                info = SandboxInfo(
                    sandbox_id="nonexistent",
                    sandbox_dir=tmp_path / "nonexistent",
                    data_dir=tmp_path / "nonexistent" / "data",
                    language="py",
                    session_id="session1",
                    created_at=datetime.utcnow(),
                )

                result = manager.destroy_sandbox(info)
                assert result is True


class TestFileOperations:
    """Test file copy and retrieval operations."""

    def test_copy_content_to_sandbox(self, tmp_path):
        """Test writing content to a sandbox."""
        with patch("shutil.which", return_value="/usr/bin/nsjail"), \
             patch("os.chown"), \
             patch("os.chmod"):
            with patch.object(SandboxManager, "__init__", lambda self: None):
                manager = SandboxManager()
                manager._nsjail_config = MagicMock()
                manager._executor = MagicMock()
                manager._base_dir = tmp_path
                manager._initialization_error = None

                info = manager.create_sandbox("session1", "py")
                result = manager.copy_content_to_sandbox(
                    info, b"hello world", "/mnt/data/test.txt", "py"
                )
                assert result is True
                assert (info.data_dir / "test.txt").read_bytes() == b"hello world"

    def test_copy_content_extracts_filename(self, tmp_path):
        """Test that copy extracts filename from full path."""
        with patch("shutil.which", return_value="/usr/bin/nsjail"), \
             patch("os.chown"), \
             patch("os.chmod"):
            with patch.object(SandboxManager, "__init__", lambda self: None):
                manager = SandboxManager()
                manager._nsjail_config = MagicMock()
                manager._executor = MagicMock()
                manager._base_dir = tmp_path
                manager._initialization_error = None

                info = manager.create_sandbox("session1", "py")
                result = manager.copy_content_to_sandbox(
                    info, b"data", "/mnt/data/subdir/file.txt", "py"
                )
                assert result is True
                # Should extract just the filename
                assert (info.data_dir / "file.txt").read_bytes() == b"data"

    def test_get_file_content_from_sandbox(self, tmp_path):
        """Test reading content from a sandbox."""
        with patch("shutil.which", return_value="/usr/bin/nsjail"), \
             patch("os.chown"), \
             patch("os.chmod"):
            with patch.object(SandboxManager, "__init__", lambda self: None):
                manager = SandboxManager()
                manager._nsjail_config = MagicMock()
                manager._executor = MagicMock()
                manager._base_dir = tmp_path
                manager._initialization_error = None

                info = manager.create_sandbox("session1", "py")
                (info.data_dir / "output.txt").write_bytes(b"result data")
                content = manager.get_file_content_from_sandbox(
                    info, "/mnt/data/output.txt"
                )
                assert content == b"result data"

    def test_get_file_content_not_found(self, tmp_path):
        """Test reading non-existent file returns None."""
        with patch("shutil.which", return_value="/usr/bin/nsjail"), \
             patch("os.chown"):
            with patch.object(SandboxManager, "__init__", lambda self: None):
                manager = SandboxManager()
                manager._nsjail_config = MagicMock()
                manager._executor = MagicMock()
                manager._base_dir = tmp_path
                manager._initialization_error = None

                info = manager.create_sandbox("session1", "py")
                content = manager.get_file_content_from_sandbox(
                    info, "/mnt/data/nonexistent.txt"
                )
                assert content is None

    def test_get_file_content_mnt_data_prefix(self, tmp_path):
        """Test reading file with /mnt/data/ prefix."""
        with patch("shutil.which", return_value="/usr/bin/nsjail"), \
             patch("os.chown"):
            with patch.object(SandboxManager, "__init__", lambda self: None):
                manager = SandboxManager()
                manager._nsjail_config = MagicMock()
                manager._executor = MagicMock()
                manager._base_dir = tmp_path
                manager._initialization_error = None

                info = manager.create_sandbox("session1", "py")
                (info.data_dir / "test.py").write_bytes(b"print('hi')")
                content = manager.get_file_content_from_sandbox(
                    info, "/mnt/data/test.py"
                )
                assert content == b"print('hi')"


class TestManagerUtility:
    """Test utility methods."""

    def test_get_user_id_for_language(self):
        """Test get_user_id_for_language returns correct IDs."""
        with patch("shutil.which", return_value="/usr/bin/nsjail"):
            with patch.object(SandboxManager, "__init__", lambda self: None):
                manager = SandboxManager()
                manager._nsjail_config = MagicMock()
                manager._executor = MagicMock()
                manager._base_dir = Path("/tmp/test")
                manager._initialization_error = None

                # Python user ID is 999
                assert manager.get_user_id_for_language("py") == 999
                # JS user ID is 1001
                assert manager.get_user_id_for_language("js") == 1001

    def test_close_is_noop(self):
        """Test close method is a no-op."""
        with patch("shutil.which", return_value="/usr/bin/nsjail"):
            with patch.object(SandboxManager, "__init__", lambda self: None):
                manager = SandboxManager()
                manager._nsjail_config = MagicMock()
                manager._executor = MagicMock()
                manager._base_dir = Path("/tmp/test")
                manager._initialization_error = None
                # Should not raise
                manager.close()

    def test_executor_property(self):
        """Test executor property returns the executor."""
        with patch("shutil.which", return_value="/usr/bin/nsjail"):
            with patch.object(SandboxManager, "__init__", lambda self: None):
                manager = SandboxManager()
                mock_executor = MagicMock()
                manager._nsjail_config = MagicMock()
                manager._executor = mock_executor
                manager._base_dir = Path("/tmp/test")
                manager._initialization_error = None

                assert manager.executor is mock_executor
