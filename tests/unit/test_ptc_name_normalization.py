"""Unit tests for PTC tool-name normalization.

Both PTC servers (Python and Bash) accept arbitrary tool names from the host
but must turn them into legal identifiers in the language they expose. The
SDK runs the same normalization on the client when generating user code, so
the two halves must agree exactly.
"""

import sys
from pathlib import Path

import pytest

# The docker/ scripts aren't a package — add the repo root so we can import
# them by module name.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))


from docker.ptc_server import _normalize_python_name  # noqa: E402
from docker.ptc_bash_server import _normalize_bash_name  # noqa: E402


class TestPythonNameNormalization:
    """Python rules: replace [-\\s] with _, strip non-alnum/_, prefix _ for
    leading digits, suffix _tool for keywords. Dots are stripped (not
    replaced) — they fall through the strip step."""

    def test_hyphen_replaced(self):
        assert _normalize_python_name("my-tool") == "my_tool"

    def test_keyword_suffixed(self):
        assert _normalize_python_name("for") == "for_tool"

    def test_leading_digit_prefixed(self):
        assert _normalize_python_name("2fast") == "_2fast"

    def test_dot_stripped_not_replaced(self):
        # Dots fall through the strip step (they're not in [-\s] and not
        # in [a-zA-Z0-9_]) so they vanish entirely.
        assert _normalize_python_name("my.tool") == "mytool"

    def test_space_replaced(self):
        assert _normalize_python_name("my tool") == "my_tool"

    def test_async_keyword(self):
        assert _normalize_python_name("async") == "async_tool"

    def test_already_valid_unchanged(self):
        assert _normalize_python_name("good_name") == "good_name"


class TestBashNameNormalization:
    """Bash rules: replace [-\\s.] with _ (note: dots ARE replaced), strip
    non-alnum/_, prefix _ for leading digits, suffix _tool for reserved
    words."""

    def test_hyphen_replaced(self):
        assert _normalize_bash_name("my-tool") == "my_tool"

    def test_reserved_suffixed(self):
        assert _normalize_bash_name("if") == "if_tool"

    def test_dot_replaced_with_underscore(self):
        # Different from Python: dots map to _, not stripped.
        assert _normalize_bash_name("my.tool") == "my_tool"

    def test_leading_digit_prefixed(self):
        assert _normalize_bash_name("2fast") == "_2fast"

    def test_function_keyword(self):
        assert _normalize_bash_name("function") == "function_tool"

    def test_space_replaced(self):
        assert _normalize_bash_name("my tool") == "my_tool"

    def test_already_valid_unchanged(self):
        assert _normalize_bash_name("good_name") == "good_name"
