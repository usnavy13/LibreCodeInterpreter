"""Unit tests for NsjailConfig builder and SandboxInfo dataclass."""

import pytest
from pathlib import Path
from datetime import datetime

from src.services.sandbox.nsjail import NsjailConfig, SandboxInfo


class TestNsjailConfigBuildArgs:
    """Test NsjailConfig.build_args() generates correct nsjail CLI arguments."""

    def test_basic_python_args(self):
        """Test basic argument generation for Python."""
        config = NsjailConfig()
        args = config.build_args(
            sandbox_dir="/tmp/sandbox/data",
            command=["python3", "code.py"],
            language="py",
            timeout=30,
        )
        assert "--mode" in args
        assert "o" in args
        assert "--cwd" in args
        assert "/mnt/data" in args
        assert "python3" in args
        assert "code.py" in args

    def test_network_disabled_by_default(self):
        """Test that network namespace is created by default (no network access)."""
        config = NsjailConfig()
        args = config.build_args(
            sandbox_dir="/tmp/sandbox/data",
            command=["python3", "code.py"],
            language="py",
        )
        # Network isolation is on by default (iface_no_lo disables loopback)
        assert "--iface_no_lo" in args
        # Should NOT have --disable_clone_newnet
        assert "--disable_clone_newnet" not in args

    def test_network_enabled(self):
        """Test network access when enabled (disable network namespace)."""
        config = NsjailConfig()
        args = config.build_args(
            sandbox_dir="/tmp/sandbox/data",
            command=["python3", "code.py"],
            language="py",
            network=True,
        )
        # When network=True, network namespace is disabled
        assert "--disable_clone_newnet" in args
        assert "--iface_no_lo" not in args

    def test_timeout_set(self):
        """Test timeout is passed correctly."""
        config = NsjailConfig()
        args = config.build_args(
            sandbox_dir="/tmp/sandbox/data",
            command=["python3", "code.py"],
            language="py",
            timeout=60,
        )
        assert "--time_limit" in args
        idx = args.index("--time_limit")
        assert args[idx + 1] == "60"

    def test_repl_mode_timeout_zero(self):
        """Test REPL mode sets timeout to 0 and enables skip_setsid."""
        config = NsjailConfig()
        args = config.build_args(
            sandbox_dir="/tmp/sandbox/data",
            command=["python3", "/opt/repl_server.py"],
            language="py",
            repl_mode=True,
        )
        assert "--time_limit" in args
        idx = args.index("--time_limit")
        assert args[idx + 1] == "0"
        assert "--skip_setsid" in args

    def test_different_languages(self):
        """Test args generation for different languages."""
        config = NsjailConfig()
        for lang in ["py", "js", "go", "java", "c", "cpp", "rs"]:
            args = config.build_args(
                sandbox_dir="/tmp/sandbox/data",
                command=["echo", "test"],
                language=lang,
            )
            assert len(args) > 0
            assert "--mode" in args
            assert "echo" in args
            assert "test" in args

    def test_capabilities_dropped_by_default(self):
        """Test capabilities are dropped (no --keep_caps flag)."""
        config = NsjailConfig()
        args = config.build_args(
            sandbox_dir="/tmp/sandbox/data",
            command=["echo", "test"],
            language="py",
        )
        # nsjail drops all caps by default. --keep_caps would KEEP them.
        assert "--keep_caps" not in args

    def test_user_namespace_disabled(self):
        """Test user namespace is disabled (Docker compatibility)."""
        config = NsjailConfig()
        args = config.build_args(
            sandbox_dir="/tmp/sandbox/data",
            command=["echo", "test"],
            language="py",
        )
        assert "--disable_clone_newuser" in args

    def test_mount_namespace_disabled(self):
        """Test mount namespace is disabled (executor handles /mnt/data via unshare)."""
        config = NsjailConfig()
        args = config.build_args(
            sandbox_dir="/tmp/sandbox/data",
            command=["echo", "test"],
            language="py",
        )
        assert "--disable_clone_newns" in args

    def test_hostname_set_to_sandbox(self):
        """Test hostname is set to 'sandbox'."""
        config = NsjailConfig()
        args = config.build_args(
            sandbox_dir="/tmp/sandbox/data",
            command=["echo", "test"],
            language="py",
        )
        assert "--hostname" in args
        idx = args.index("--hostname")
        assert args[idx + 1] == "sandbox"

    def test_proc_disabled(self):
        """Test proc is disabled."""
        config = NsjailConfig()
        args = config.build_args(
            sandbox_dir="/tmp/sandbox/data",
            command=["echo", "test"],
            language="py",
        )
        assert "--disable_proc" in args

    def test_command_separator(self):
        """Test command separator '--' is present before the command."""
        config = NsjailConfig()
        args = config.build_args(
            sandbox_dir="/tmp/sandbox/data",
            command=["python3", "code.py"],
            language="py",
        )
        assert "--" in args
        separator_idx = args.index("--")
        assert args[separator_idx + 1] == "python3"
        assert args[separator_idx + 2] == "code.py"

    def test_env_vars_passed(self):
        """Test environment variables are passed correctly."""
        config = NsjailConfig()
        args = config.build_args(
            sandbox_dir="/tmp/sandbox/data",
            command=["echo", "test"],
            language="py",
            env={"MY_VAR": "my_value", "ANOTHER": "val2"},
        )
        assert "--env" in args
        env_indices = [i for i, a in enumerate(args) if a == "--env"]
        env_values = [args[i + 1] for i in env_indices]
        assert "MY_VAR=my_value" in env_values
        assert "ANOTHER=val2" in env_values

    def test_user_id_set(self):
        """Test user and group IDs are set."""
        config = NsjailConfig()
        args = config.build_args(
            sandbox_dir="/tmp/sandbox/data",
            command=["echo", "test"],
            language="py",
        )
        assert "--user" in args
        assert "--group" in args

    def test_cwd_is_mnt_data(self):
        """Test working directory is /mnt/data."""
        config = NsjailConfig()
        args = config.build_args(
            sandbox_dir="/tmp/sandbox/data",
            command=["echo", "test"],
            language="py",
        )
        idx = args.index("--cwd")
        assert args[idx + 1] == "/mnt/data"


class TestSandboxInfo:
    """Test SandboxInfo dataclass."""

    def test_id_property(self):
        """Test id property returns sandbox_id."""
        info = SandboxInfo(
            sandbox_id="abc123",
            sandbox_dir=Path("/tmp/abc"),
            data_dir=Path("/tmp/abc/data"),
            language="py",
            session_id="sess1",
            created_at=datetime.utcnow(),
        )
        assert info.id == "abc123"

    def test_default_values(self):
        """Test default values are set correctly."""
        info = SandboxInfo(
            sandbox_id="abc",
            sandbox_dir=Path("/tmp/abc"),
            data_dir=Path("/tmp/abc/data"),
            language="py",
            session_id="s1",
            created_at=datetime.utcnow(),
        )
        assert info.repl_mode is False
        assert info.labels == {}

    def test_repl_mode_set(self):
        """Test repl_mode can be set."""
        info = SandboxInfo(
            sandbox_id="abc",
            sandbox_dir=Path("/tmp/abc"),
            data_dir=Path("/tmp/abc/data"),
            language="py",
            session_id="s1",
            created_at=datetime.utcnow(),
            repl_mode=True,
        )
        assert info.repl_mode is True

    def test_labels_set(self):
        """Test labels can be set."""
        labels = {"key1": "val1", "key2": "val2"}
        info = SandboxInfo(
            sandbox_id="abc",
            sandbox_dir=Path("/tmp/abc"),
            data_dir=Path("/tmp/abc/data"),
            language="py",
            session_id="s1",
            created_at=datetime.utcnow(),
            labels=labels,
        )
        assert info.labels == labels

    def test_fields_stored(self):
        """Test all fields are stored correctly."""
        now = datetime.utcnow()
        info = SandboxInfo(
            sandbox_id="sandbox-xyz",
            sandbox_dir=Path("/var/sandboxes/xyz"),
            data_dir=Path("/var/sandboxes/xyz/data"),
            language="go",
            session_id="session-456",
            created_at=now,
        )
        assert info.sandbox_id == "sandbox-xyz"
        assert info.sandbox_dir == Path("/var/sandboxes/xyz")
        assert info.data_dir == Path("/var/sandboxes/xyz/data")
        assert info.language == "go"
        assert info.session_id == "session-456"
        assert info.created_at == now
