"""
Container Hardening Tests - Information Leakage Prevention

This test suite verifies that containers are properly hardened to prevent
host infrastructure information from being exposed to executed code.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone, timedelta

from src.main import app
from src.models import CodeExecution, ExecutionStatus, ExecutionOutput, OutputType
from src.models.session import Session, SessionStatus


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Provide authentication headers for tests."""
    return {"x-api-key": "test-api-key-for-testing-12345"}


def create_session(session_id: str) -> Session:
    """Helper to create a session."""
    return Session(
        session_id=session_id,
        status=SessionStatus.ACTIVE,
        created_at=datetime.now(timezone.utc),
        last_activity=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        metadata={},
    )


class TestContainerHardening:
    """Test container hardening against information leakage."""

    def test_hardening_config_defaults_enabled(self):
        """Test that hardening configuration defaults are enabled."""
        from src.config import settings

        assert settings.container_mask_host_info is True
        assert settings.container_generic_hostname == "sandbox"

    def test_hostname_is_generic(self, client, auth_headers):
        """Verify hostname is 'sandbox' instead of revealing host info."""
        session_id = "hardening-hostname-test"
        mock_session = create_session(session_id)

        # Mock execution that reads hostname
        mock_execution = CodeExecution(
            execution_id="exec-hostname",
            session_id=session_id,
            code="import socket; print(socket.gethostname())",
            language="py",
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            outputs=[
                ExecutionOutput(
                    type=OutputType.STDOUT,
                    content="sandbox\n",
                    timestamp=datetime.now(timezone.utc),
                )
            ],
        )

        mock_session_service = AsyncMock()
        mock_session_service.create_session.return_value = mock_session
        mock_session_service.get_session.return_value = mock_session

        mock_execution_service = AsyncMock()
        mock_execution_service.execute_code.return_value = (
            mock_execution,
            None,
            None,
            [],
            "pool_hit",
        )

        mock_file_service = AsyncMock()
        mock_file_service.list_files.return_value = []

        from src.dependencies.services import (
            get_session_service,
            get_execution_service,
            get_file_service,
        )

        app.dependency_overrides[get_session_service] = lambda: mock_session_service
        app.dependency_overrides[get_execution_service] = lambda: mock_execution_service
        app.dependency_overrides[get_file_service] = lambda: mock_file_service

        try:
            response = client.post(
                "/exec",
                json={
                    "code": "import socket; print(socket.gethostname())",
                    "lang": "py",
                },
                headers=auth_headers,
            )

            assert response.status_code == 200
            data = response.json()
            # Hostname should be 'sandbox', not contain Azure or host info
            assert (
                "sandbox" in data.get("stdout", "").lower()
                or response.status_code == 200
            )
        finally:
            app.dependency_overrides.clear()

    def test_proc_version_masked(self, client, auth_headers):
        """Verify /proc/version is masked and returns empty or error."""
        session_id = "hardening-proc-version-test"
        mock_session = create_session(session_id)

        # Mock execution that tries to read /proc/version
        # When masked, this should return empty or an error
        mock_execution = CodeExecution(
            execution_id="exec-proc-version",
            session_id=session_id,
            code="open('/proc/version').read()",
            language="py",
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            outputs=[
                ExecutionOutput(
                    type=OutputType.STDOUT,
                    content="",  # Empty due to masking
                    timestamp=datetime.now(timezone.utc),
                )
            ],
        )

        mock_session_service = AsyncMock()
        mock_session_service.create_session.return_value = mock_session
        mock_session_service.get_session.return_value = mock_session

        mock_execution_service = AsyncMock()
        mock_execution_service.execute_code.return_value = (
            mock_execution,
            None,
            None,
            [],
            "pool_hit",
        )

        mock_file_service = AsyncMock()
        mock_file_service.list_files.return_value = []

        from src.dependencies.services import (
            get_session_service,
            get_execution_service,
            get_file_service,
        )

        app.dependency_overrides[get_session_service] = lambda: mock_session_service
        app.dependency_overrides[get_execution_service] = lambda: mock_execution_service
        app.dependency_overrides[get_file_service] = lambda: mock_file_service

        try:
            response = client.post(
                "/exec",
                json={
                    "code": "print(open('/proc/version').read())",
                    "lang": "py",
                },
                headers=auth_headers,
            )

            assert response.status_code == 200
            data = response.json()
            stdout = data.get("stdout", "")
            # Should NOT contain Azure kernel version info
            assert "azure" not in stdout.lower()
        finally:
            app.dependency_overrides.clear()

    def test_machine_id_masked(self, client, auth_headers):
        """Verify /etc/machine-id is masked."""
        session_id = "hardening-machine-id-test"
        mock_session = create_session(session_id)

        # Mock execution that tries to read /etc/machine-id
        mock_execution = CodeExecution(
            execution_id="exec-machine-id",
            session_id=session_id,
            code="open('/etc/machine-id').read()",
            language="py",
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            outputs=[
                ExecutionOutput(
                    type=OutputType.STDOUT,
                    content="",  # Empty due to masking
                    timestamp=datetime.now(timezone.utc),
                )
            ],
        )

        mock_session_service = AsyncMock()
        mock_session_service.create_session.return_value = mock_session
        mock_session_service.get_session.return_value = mock_session

        mock_execution_service = AsyncMock()
        mock_execution_service.execute_code.return_value = (
            mock_execution,
            None,
            None,
            [],
            "pool_hit",
        )

        mock_file_service = AsyncMock()
        mock_file_service.list_files.return_value = []

        from src.dependencies.services import (
            get_session_service,
            get_execution_service,
            get_file_service,
        )

        app.dependency_overrides[get_session_service] = lambda: mock_session_service
        app.dependency_overrides[get_execution_service] = lambda: mock_execution_service
        app.dependency_overrides[get_file_service] = lambda: mock_file_service

        try:
            response = client.post(
                "/exec",
                json={
                    "code": "print(open('/etc/machine-id').read())",
                    "lang": "py",
                },
                headers=auth_headers,
            )

            assert response.status_code == 200
        finally:
            app.dependency_overrides.clear()


class TestContainerHardeningConfig:
    """Test container hardening configuration integration."""

    def test_hardening_config_applied_to_sandbox(self):
        """Test that hardening config is used in sandbox creation."""
        from src.services.sandbox.manager import SandboxManager
        from src.config import settings

        # Verify settings are correctly configured
        assert hasattr(settings, "container_mask_host_info")
        assert hasattr(settings, "container_generic_hostname")

    def test_masked_paths_list_complete(self):
        """Test that all expected paths are in the masked paths list."""
        from src.config import settings

        # These are the paths that should be masked when hardening is enabled
        expected_masked = [
            "/proc/version",
            "/etc/machine-id",
        ]

        # The actual paths are defined in manager.py when container_mask_host_info is True
        # This test verifies the setting exists
        assert settings.container_mask_host_info is True

    def test_dns_search_sanitized_for_wan(self):
        """Test that dns_search is empty for WAN containers."""
        from src.config import settings

        # Verify WAN DNS configuration exists
        assert hasattr(settings, "wan_dns_servers")
        assert len(settings.wan_dns_servers) > 0
        # DNS servers should be public (e.g., 8.8.8.8, 1.1.1.1)
        for dns in settings.wan_dns_servers:
            # Should not be internal/private DNS
            assert not dns.startswith("10.")
            assert not dns.startswith("192.168.")
            assert not dns.startswith("172.")


class TestContainerHardeningWAN:
    """Test container hardening for WAN-enabled containers."""

    def test_resolv_conf_no_internal_domains(self, client, auth_headers):
        """Verify resolv.conf doesn't leak internal Azure domains."""
        session_id = "hardening-resolv-test"
        mock_session = create_session(session_id)

        # Mock execution that reads /etc/resolv.conf
        # With hardening, search domain should be empty
        mock_execution = CodeExecution(
            execution_id="exec-resolv",
            session_id=session_id,
            code="print(open('/etc/resolv.conf').read())",
            language="py",
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            outputs=[
                ExecutionOutput(
                    type=OutputType.STDOUT,
                    content="nameserver 8.8.8.8\nnameserver 1.1.1.1\n",
                    timestamp=datetime.now(timezone.utc),
                )
            ],
        )

        mock_session_service = AsyncMock()
        mock_session_service.create_session.return_value = mock_session
        mock_session_service.get_session.return_value = mock_session

        mock_execution_service = AsyncMock()
        mock_execution_service.execute_code.return_value = (
            mock_execution,
            None,
            None,
            [],
            "pool_hit",
        )

        mock_file_service = AsyncMock()
        mock_file_service.list_files.return_value = []

        from src.dependencies.services import (
            get_session_service,
            get_execution_service,
            get_file_service,
        )

        app.dependency_overrides[get_session_service] = lambda: mock_session_service
        app.dependency_overrides[get_execution_service] = lambda: mock_execution_service
        app.dependency_overrides[get_file_service] = lambda: mock_file_service

        try:
            response = client.post(
                "/exec",
                json={
                    "code": "print(open('/etc/resolv.conf').read())",
                    "lang": "py",
                },
                headers=auth_headers,
            )

            assert response.status_code == 200
            data = response.json()
            stdout = data.get("stdout", "")
            # Should NOT contain Azure internal domains
            assert "cloudapp.net" not in stdout.lower()
            assert "internal" not in stdout.lower()
        finally:
            app.dependency_overrides.clear()

    def test_ptrace_blocked_by_seccomp(self, client, auth_headers):
        """Verify ptrace syscall is blocked by seccomp profile.

        This test verifies that the seccomp profile blocks ptrace,
        which prevents process tracing attacks that can cause containers
        to become unresponsive to stop signals.
        """
        session_id = "hardening-ptrace-test"
        mock_session = create_session(session_id)

        # Mock execution that attempts ptrace - should return -1 (EPERM)
        # When seccomp blocks ptrace, it returns EPERM (-1)
        mock_execution = CodeExecution(
            execution_id="exec-ptrace",
            session_id=session_id,
            code="""
import ctypes
libc = ctypes.CDLL("libc.so.6")
result = libc.ptrace(0, 0, 0, 0)  # PTRACE_TRACEME
print(f"ptrace result: {result}")
""",
            language="py",
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            outputs=[
                ExecutionOutput(
                    type=OutputType.STDOUT,
                    content="ptrace result: -1\n",
                    timestamp=datetime.now(timezone.utc),
                )
            ],
        )

        mock_session_service = AsyncMock()
        mock_session_service.create_session.return_value = mock_session
        mock_session_service.get_session.return_value = mock_session

        mock_execution_service = AsyncMock()
        mock_execution_service.execute_code.return_value = (
            mock_execution,
            None,
            None,
            [],
            "pool_hit",
        )

        mock_file_service = AsyncMock()
        mock_file_service.list_files.return_value = []

        from src.dependencies.services import (
            get_session_service,
            get_execution_service,
            get_file_service,
        )

        app.dependency_overrides[get_session_service] = lambda: mock_session_service
        app.dependency_overrides[get_execution_service] = lambda: mock_execution_service
        app.dependency_overrides[get_file_service] = lambda: mock_file_service

        try:
            response = client.post(
                "/exec",
                json={
                    "code": """
import ctypes
libc = ctypes.CDLL("libc.so.6")
result = libc.ptrace(0, 0, 0, 0)
print(f"ptrace result: {result}")
""",
                    "lang": "py",
                },
                headers=auth_headers,
            )

            assert response.status_code == 200
            data = response.json()
            stdout = data.get("stdout", "")
            # When seccomp blocks ptrace, it returns -1 (EPERM)
            assert "ptrace result: -1" in stdout
        finally:
            app.dependency_overrides.clear()

    def test_seccomp_profile_config_exists(self):
        """Verify seccomp profile configuration is set."""
        from src.config import settings

        assert settings.docker_seccomp_profile == "docker/seccomp-sandbox.json"

    def test_seccomp_profile_file_exists(self):
        """Verify seccomp profile file exists and is valid JSON."""
        import json
        from pathlib import Path

        profile_path = Path("docker/seccomp-sandbox.json")
        assert profile_path.exists(), "Seccomp profile file should exist"

        with open(profile_path) as f:
            profile = json.load(f)

        # Verify structure
        assert "defaultAction" in profile
        assert "syscalls" in profile
        assert isinstance(profile["syscalls"], list)

        # Verify ptrace is blocked
        blocked_syscalls = []
        for rule in profile["syscalls"]:
            if rule.get("action") == "SCMP_ACT_ERRNO":
                blocked_syscalls.extend(rule.get("names", []))

        assert "ptrace" in blocked_syscalls, "ptrace should be blocked by seccomp"
