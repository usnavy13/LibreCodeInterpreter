"""Unit tests for SandboxExecutor."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from src.services.sandbox.executor import SandboxExecutor
from src.services.sandbox.nsjail import NsjailConfig


class TestBuildSanitizedEnv:
    """Test _build_sanitized_env method."""

    def test_python_env(self):
        """Test sanitized env for Python."""
        config = NsjailConfig()
        executor = SandboxExecutor(config)
        env = executor._build_sanitized_env("py")
        assert "PYTHONUNBUFFERED" in env
        assert env["PYTHONUNBUFFERED"] == "1"
        assert "PATH" in env
        assert "PYTHONDONTWRITEBYTECODE" in env
        assert "PYTHONPATH" in env
        assert "MPLBACKEND" in env

    def test_javascript_env(self):
        """Test sanitized env for JavaScript."""
        config = NsjailConfig()
        executor = SandboxExecutor(config)
        env = executor._build_sanitized_env("js")
        assert "NODE_PATH" in env
        assert "PATH" in env

    def test_typescript_env(self):
        """Test sanitized env for TypeScript."""
        config = NsjailConfig()
        executor = SandboxExecutor(config)
        env = executor._build_sanitized_env("ts")
        assert "NODE_PATH" in env

    def test_go_env(self):
        """Test sanitized env for Go."""
        config = NsjailConfig()
        executor = SandboxExecutor(config)
        env = executor._build_sanitized_env("go")
        assert "GO111MODULE" in env
        assert "GOCACHE" in env

    def test_java_env(self):
        """Test sanitized env for Java."""
        config = NsjailConfig()
        executor = SandboxExecutor(config)
        env = executor._build_sanitized_env("java")
        assert "CLASSPATH" in env
        assert "JAVA_OPTS" in env

    def test_c_env(self):
        """Test sanitized env for C."""
        config = NsjailConfig()
        executor = SandboxExecutor(config)
        env = executor._build_sanitized_env("c")
        assert "CC" in env
        assert env["CC"] == "gcc"

    def test_cpp_env(self):
        """Test sanitized env for C++."""
        config = NsjailConfig()
        executor = SandboxExecutor(config)
        env = executor._build_sanitized_env("cpp")
        assert "CXX" in env
        assert env["CXX"] == "g++"

    def test_rust_env(self):
        """Test sanitized env for Rust."""
        config = NsjailConfig()
        executor = SandboxExecutor(config)
        env = executor._build_sanitized_env("rs")
        assert "CARGO_HOME" in env
        assert "RUSTUP_HOME" in env

    def test_php_env(self):
        """Test sanitized env for PHP."""
        config = NsjailConfig()
        executor = SandboxExecutor(config)
        env = executor._build_sanitized_env("php")
        assert "PHP_INI_SCAN_DIR" in env

    def test_r_env(self):
        """Test sanitized env for R."""
        config = NsjailConfig()
        executor = SandboxExecutor(config)
        env = executor._build_sanitized_env("r")
        assert "R_LIBS_USER" in env

    def test_fortran_env(self):
        """Test sanitized env for Fortran."""
        config = NsjailConfig()
        executor = SandboxExecutor(config)
        env = executor._build_sanitized_env("f90")
        assert "FC" in env
        assert env["FC"] == "gfortran"

    def test_unknown_language_has_base_env(self):
        """Test unknown language gets base env only."""
        config = NsjailConfig()
        executor = SandboxExecutor(config)
        env = executor._build_sanitized_env("unknown")
        assert "PATH" in env
        assert "HOME" in env
        assert "TMPDIR" in env
        # Should not have language-specific vars
        assert "PYTHONUNBUFFERED" not in env
        assert "NODE_PATH" not in env

    def test_none_language_has_base_env(self):
        """Test None language gets base env only."""
        config = NsjailConfig()
        executor = SandboxExecutor(config)
        env = executor._build_sanitized_env(None)
        assert "PATH" in env
        assert "HOME" in env

    def test_base_env_always_present(self):
        """Test base env vars are always present."""
        config = NsjailConfig()
        executor = SandboxExecutor(config)
        for lang in ["py", "js", "go", "java", "c", "cpp", "rs", "r", "f90"]:
            env = executor._build_sanitized_env(lang)
            assert "HOME" in env
            assert env["HOME"] == "/tmp"
            assert "TMPDIR" in env
            assert env["TMPDIR"] == "/tmp"


class TestSanitizeOutput:
    """Test _sanitize_output method."""

    def test_normal_output(self):
        """Test normal output is preserved."""
        config = NsjailConfig()
        executor = SandboxExecutor(config)
        result = executor._sanitize_output(b"hello world\n")
        assert result == "hello world\n"

    def test_unicode_output(self):
        """Test unicode output is handled."""
        config = NsjailConfig()
        executor = SandboxExecutor(config)
        result = executor._sanitize_output("hello 世界\n".encode("utf-8"))
        assert "hello 世界" in result

    def test_truncates_large_output(self):
        """Test large output is truncated."""
        config = NsjailConfig()
        executor = SandboxExecutor(config)
        large_output = b"x" * (1024 * 1024 + 100)
        result = executor._sanitize_output(large_output)
        assert "[Output truncated" in result

    def test_strips_control_chars(self):
        """Test control characters are stripped."""
        config = NsjailConfig()
        executor = SandboxExecutor(config)
        result = executor._sanitize_output(b"hello\x00\x01world")
        assert "\x00" not in result
        assert "\x01" not in result
        assert "hello" in result
        assert "world" in result

    def test_preserves_newlines(self):
        """Test newlines are preserved."""
        config = NsjailConfig()
        executor = SandboxExecutor(config)
        result = executor._sanitize_output(b"line1\nline2\n")
        assert result == "line1\nline2\n"

    def test_preserves_tabs(self):
        """Test tabs are preserved."""
        config = NsjailConfig()
        executor = SandboxExecutor(config)
        result = executor._sanitize_output(b"col1\tcol2\n")
        assert "\t" in result

    def test_empty_output(self):
        """Test empty output."""
        config = NsjailConfig()
        executor = SandboxExecutor(config)
        result = executor._sanitize_output(b"")
        assert result == ""

    def test_invalid_utf8_replaced(self):
        """Test invalid UTF-8 bytes are replaced."""
        config = NsjailConfig()
        executor = SandboxExecutor(config)
        result = executor._sanitize_output(b"hello \xff\xfe world")
        # Should not raise, invalid bytes replaced
        assert "hello" in result
        assert "world" in result


class TestEscapeEnvValue:
    """Test _escape_env_value method."""

    def test_simple_value(self):
        """Test simple value escaping."""
        config = NsjailConfig()
        executor = SandboxExecutor(config)
        result = executor._escape_env_value("simple")
        assert result == "'simple'"

    def test_value_with_single_quotes(self):
        """Test value with single quotes."""
        config = NsjailConfig()
        executor = SandboxExecutor(config)
        result = executor._escape_env_value("it's")
        assert "it" in result
        assert "s" in result

    def test_empty_value(self):
        """Test empty value."""
        config = NsjailConfig()
        executor = SandboxExecutor(config)
        result = executor._escape_env_value("")
        assert result == "''"
