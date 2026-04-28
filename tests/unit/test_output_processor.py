"""Unit tests for the OutputProcessor."""

import pytest
from src.services.execution.output import OutputProcessor


class TestSanitizeFilename:
    """Tests for the sanitize_filename method."""

    def test_spaces_replaced_with_underscores(self):
        """Test that spaces are replaced with underscores."""
        result = OutputProcessor.sanitize_filename("file with spaces.txt")
        assert result == "file_with_spaces.txt"

    def test_parentheses_replaced_with_underscores(self):
        """Test that parentheses are replaced with underscores."""
        result = OutputProcessor.sanitize_filename("manufacturing_analysis (v2).xlsx")
        assert result == "manufacturing_analysis__v2_.xlsx"

    def test_special_characters_replaced(self):
        """Test that special characters are replaced with underscores."""
        result = OutputProcessor.sanitize_filename("special@chars#here!.pdf")
        assert result == "special_chars_here_.pdf"

    def test_already_valid_unchanged(self):
        """Test that already valid filenames are unchanged."""
        result = OutputProcessor.sanitize_filename("already-valid.txt")
        assert result == "already-valid.txt"

    def test_uppercase_preserved(self):
        """Test that uppercase letters are preserved."""
        result = OutputProcessor.sanitize_filename("UPPERCASE.TXT")
        assert result == "UPPERCASE.TXT"

    def test_numbers_preserved(self):
        """Test that numbers are preserved."""
        result = OutputProcessor.sanitize_filename("123numbers.doc")
        assert result == "123numbers.doc"

    def test_hidden_file_prefixed(self):
        """Test that hidden files (starting with dot) get underscore prefix."""
        result = OutputProcessor.sanitize_filename(".hidden")
        assert result == "_.hidden"

    def test_empty_string_returns_underscore(self):
        """Test that empty string returns underscore."""
        result = OutputProcessor.sanitize_filename("")
        assert result == "_"

    def test_none_returns_underscore(self):
        """Test that None returns underscore."""
        result = OutputProcessor.sanitize_filename(None)
        assert result == "_"

    def test_path_traversal_stripped(self):
        """Test that path traversal attempts are stripped."""
        result = OutputProcessor.sanitize_filename("../../../etc/passwd")
        assert result == "passwd"

    def test_absolute_path_stripped(self):
        """Test that absolute paths are stripped to basename."""
        result = OutputProcessor.sanitize_filename("/absolute/path/file.txt")
        assert result == "file.txt"

    def test_unicode_characters_replaced(self):
        """Test that non-ASCII characters are replaced."""
        result = OutputProcessor.sanitize_filename("résumé.docx")
        assert result == "r_sum_.docx"

    def test_brackets_replaced(self):
        """Test that brackets are replaced with underscores."""
        result = OutputProcessor.sanitize_filename("[brackets].txt")
        assert result == "_brackets_.txt"

    def test_leading_parenthesis_prefixed(self):
        """Test that filename starting with parenthesis is handled."""
        result = OutputProcessor.sanitize_filename("(parentheses).txt")
        assert result == "_parentheses_.txt"

    def test_dashes_preserved(self):
        """Test that dashes are preserved."""
        result = OutputProcessor.sanitize_filename("file-name.with-dashes.txt")
        assert result == "file-name.with-dashes.txt"

    def test_dots_preserved(self):
        """Test that dots in filename are preserved."""
        result = OutputProcessor.sanitize_filename("file.name.multiple.dots.txt")
        assert result == "file.name.multiple.dots.txt"

    def test_simple_filename_unchanged(self):
        """Test that simple alphanumeric filename is unchanged."""
        result = OutputProcessor.sanitize_filename("SimpleFile123.pdf")
        assert result == "SimpleFile123.pdf"

    def test_long_filename_truncated(self):
        """Test that filenames over 255 chars are truncated with hash suffix."""
        long_name = "a" * 300 + ".txt"
        result = OutputProcessor.sanitize_filename(long_name)
        # Should be 255 chars or less
        assert len(result) <= 255
        # Should end with .txt
        assert result.endswith(".txt")
        # Should have a random suffix before extension
        assert "-" in result


class TestSanitizeRelativePath:
    """Tests for sanitize_relative_path — used wherever filenames may legitimately
    contain subdirectories (LibreChat skill bundles, nested artifacts)."""

    def test_simple_filename_unchanged(self):
        assert OutputProcessor.sanitize_relative_path("foo.png") == "foo.png"

    def test_subdirs_preserved(self):
        assert (
            OutputProcessor.sanitize_relative_path("charts/foo.png") == "charts/foo.png"
        )

    def test_deep_subdirs_preserved(self):
        assert (
            OutputProcessor.sanitize_relative_path("a/b/c/d/file.txt")
            == "a/b/c/d/file.txt"
        )

    def test_each_segment_sanitized(self):
        assert (
            OutputProcessor.sanitize_relative_path("my charts/foo bar.png")
            == "my_charts/foo_bar.png"
        )

    def test_traversal_segments_dropped(self):
        # `..` is dropped per-segment; remaining segments survive.
        assert OutputProcessor.sanitize_relative_path("a/../b/c.txt") == "a/b/c.txt"

    def test_only_traversal_returns_underscore(self):
        assert OutputProcessor.sanitize_relative_path("../../..") == "_"

    def test_leading_slash_stripped(self):
        assert (
            OutputProcessor.sanitize_relative_path("/charts/foo.png")
            == "charts/foo.png"
        )

    def test_trailing_slash_dropped(self):
        assert OutputProcessor.sanitize_relative_path("charts/") == "charts"

    def test_consecutive_slashes_collapsed(self):
        assert (
            OutputProcessor.sanitize_relative_path("charts//foo.png")
            == "charts/foo.png"
        )

    def test_empty_string_returns_underscore(self):
        assert OutputProcessor.sanitize_relative_path("") == "_"

    def test_just_slash_returns_underscore(self):
        assert OutputProcessor.sanitize_relative_path("/") == "_"

    def test_backslashes_treated_as_separators(self):
        assert (
            OutputProcessor.sanitize_relative_path("charts\\foo.png")
            == "charts/foo.png"
        )

    def test_librechat_skill_bundle_pattern(self):
        # The exact shape LibreChat sends for skill priming uploads.
        assert (
            OutputProcessor.sanitize_relative_path("skills/foo/SKILL.md")
            == "skills/foo/SKILL.md"
        )

    def test_sanitize_filename_unchanged_for_basename_callers(self):
        """Regression: sanitize_filename still flattens (legacy upload behavior)."""
        # Existing single-call sites rely on this.
        assert OutputProcessor.sanitize_filename("path/to/file.txt") == "file.txt"
