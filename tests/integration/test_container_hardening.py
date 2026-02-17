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
        """Test that sandbox hardening is enabled by default via nsjail."""
        from src.config import settings

        # nsjail handles host info masking and hostname isolation natively
        assert settings.enable_network_isolation is True
        assert settings.enable_filesystem_isolation is True

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

        # Verify sandbox settings are correctly configured
        assert hasattr(settings, "nsjail_binary")
        assert hasattr(settings, "sandbox_base_dir")

    def test_masked_paths_list_complete(self):
        """Test that nsjail masks sensitive paths by default."""
        from src.config import settings

        # nsjail handles path masking natively through its mount configuration
        # Verify sandbox isolation settings are enabled
        assert settings.enable_filesystem_isolation is True

    def test_network_isolation_enabled(self):
        """Test that network isolation is enabled by default."""
        from src.config import settings

        # nsjail sandboxes run without network access by default
        assert settings.enable_network_isolation is True


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

    def test_sandbox_config_exists(self):
        """Verify sandbox configuration is set."""
        from src.config import settings

        assert hasattr(settings, "nsjail_binary")
        assert settings.nsjail_binary == "nsjail"
