"""Unit tests for file upload type validation."""

import pytest
from unittest.mock import patch, MagicMock


class TestIsFileAllowed:
    """Test the is_file_allowed settings method."""

    def test_allowed_extension_passes(self):
        """Test that allowed file extensions pass validation."""
        from src.config import settings

        # Text and code files
        assert settings.is_file_allowed("test.txt") is True
        assert settings.is_file_allowed("script.py") is True
        assert settings.is_file_allowed("data.json") is True
        assert settings.is_file_allowed("code.js") is True
        assert settings.is_file_allowed("notes.md") is True

        # Documents
        assert settings.is_file_allowed("document.pdf") is True
        assert settings.is_file_allowed("report.docx") is True
        assert settings.is_file_allowed("spreadsheet.xlsx") is True

        # Images
        assert settings.is_file_allowed("image.png") is True
        assert settings.is_file_allowed("photo.jpg") is True
        assert settings.is_file_allowed("icon.svg") is True

        # Archives
        assert settings.is_file_allowed("archive.zip") is True

    def test_blocked_extension_fails(self):
        """Test that blocked file extensions fail validation."""
        from src.config import settings

        # These are not in allowed_file_extensions
        assert settings.is_file_allowed("malware.exe") is False
        assert settings.is_file_allowed("library.dll") is False
        assert settings.is_file_allowed("binary.bin") is False
        assert settings.is_file_allowed("shared.so") is False
        assert settings.is_file_allowed("dynamic.dylib") is False

    def test_blocked_pattern_matches(self):
        """Test that blocked patterns are enforced."""
        from src.config import settings

        # Test blocked_file_patterns (*.exe, *.dll, *.so, *.dylib, *.bin)
        assert settings.is_file_allowed("anything.exe") is False
        assert settings.is_file_allowed("anything.dll") is False
        assert settings.is_file_allowed("anything.bin") is False

    def test_case_insensitive_extension(self):
        """Test that extension checking is case insensitive."""
        from src.config import settings

        # Allowed extensions should work regardless of case
        assert settings.is_file_allowed("test.TXT") is True
        assert settings.is_file_allowed("test.Txt") is True
        assert settings.is_file_allowed("script.PY") is True

        # Blocked extensions should be blocked regardless of case
        assert settings.is_file_allowed("malware.EXE") is False
        assert settings.is_file_allowed("malware.Exe") is False

    def test_file_without_extension(self):
        """Test handling of files without extensions."""
        from src.config import settings

        # Files without extensions should be allowed (no extension to block)
        # The is_file_allowed method returns True if extension is empty
        assert settings.is_file_allowed("Makefile") is True
        assert settings.is_file_allowed("Dockerfile") is True
        assert settings.is_file_allowed("README") is True

    def test_empty_filename(self):
        """Test handling of empty filename."""
        from src.config import settings

        # Empty filename should be allowed (no extension to check)
        assert settings.is_file_allowed("") is True

    def test_double_extension(self):
        """Test files with double extensions."""
        from src.config import settings

        # Only the last extension matters
        assert settings.is_file_allowed("archive.tar.gz") is False  # .gz not in allowed
        assert settings.is_file_allowed("script.test.py") is True  # .py is allowed

    def test_hidden_files(self):
        """Test hidden files (starting with dot)."""
        from src.config import settings

        assert settings.is_file_allowed(".gitignore") is True  # No extension
        assert settings.is_file_allowed(".env") is True  # No extension
        assert settings.is_file_allowed(".config.json") is True  # .json allowed
