"""Integration tests for AUTH_ENABLED=false (operator-controlled bypass).

When AUTH_ENABLED=false, requests to user endpoints (/exec, /upload, etc.)
no longer require x-api-key. This is for deployments behind a trusted
network boundary where auth is enforced at a layer above us. Admin
endpoints (/api/v1/admin/*) MUST still require the master key.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_services():
    from src.dependencies.services import (
        get_session_service,
        get_execution_service,
        get_file_service,
        get_state_service,
        get_state_archival_service,
    )

    mocks = {
        get_session_service: AsyncMock(),
        get_execution_service: AsyncMock(),
        get_file_service: AsyncMock(),
        get_state_service: AsyncMock(),
        get_state_archival_service: AsyncMock(),
    }
    for dep, mock in mocks.items():
        app.dependency_overrides[dep] = lambda m=mock: m

    yield mocks
    app.dependency_overrides.clear()


@pytest.fixture
def auth_disabled(monkeypatch):
    """Flip AUTH_ENABLED off for the duration of the test."""
    monkeypatch.setattr("src.middleware.security.settings.auth_enabled", False)
    monkeypatch.setattr("src.dependencies.auth.settings.auth_enabled", False)
    yield


class TestUserEndpointsWithAuthDisabled:
    def test_no_x_api_key_succeeds(self, client, mock_services, auth_disabled):
        """No header at all => still gets past auth."""
        response = client.get("/files/some-session-id")
        assert response.status_code != 401, response.text

    def test_invalid_x_api_key_succeeds(self, client, mock_services, auth_disabled):
        """Invalid key is still accepted because the check is bypassed."""
        response = client.get(
            "/files/some-session-id",
            headers={"x-api-key": "literally-not-a-real-key"},
        )
        assert response.status_code != 401, response.text

    def test_health_endpoint_without_header(self, client, auth_disabled):
        """Health endpoints with verify_api_key dep work without a header."""
        # /health/redis uses Depends(verify_api_key); should now pass without header
        response = client.get("/health/redis")
        # Endpoint returns 200 or 503 depending on Redis state, but never 401
        assert response.status_code != 401, response.text


class TestAdminEndpointsStillRequireMasterKey:
    def test_admin_keys_without_master_key_rejected(
        self, client, mock_services, auth_disabled
    ):
        """AUTH_ENABLED=false must NOT relax master-key requirement on admin paths."""
        with patch("src.middleware.security.settings") as ms:
            ms.auth_enabled = False
            ms.master_api_key = "master-secret-32chars-aaaaaaaaaa"
            response = client.get("/api/v1/admin/keys")
            # No master key => 401 (or 403 depending on the exact code path)
            assert response.status_code in (401, 403), response.text

    def test_admin_keys_with_wrong_master_key_rejected(
        self, client, mock_services, auth_disabled
    ):
        with patch("src.middleware.security.settings") as ms:
            ms.auth_enabled = False
            ms.master_api_key = "master-secret-32chars-aaaaaaaaaa"
            response = client.get(
                "/api/v1/admin/keys",
                headers={"x-api-key": "wrong-master-key"},
            )
            assert response.status_code in (401, 403), response.text


class TestDashboardSkipUnchanged:
    def test_dashboard_html_loads_without_master_key_when_auth_disabled(
        self, client, auth_disabled
    ):
        """The /admin-dashboard skip-auth path is unchanged; HTML loads."""
        # Dashboard route returns the HTML shell; no auth required for the shell itself
        response = client.get("/admin-dashboard/")
        # Either 200 (HTML served), 404 (route shape), or 405 (method) — but never 401
        assert response.status_code != 401, response.text

    def test_dashboard_admin_api_still_requires_master_key(
        self, client, mock_services, auth_disabled
    ):
        """Even with AUTH_ENABLED=false, /api/v1/admin/* still locked behind master key."""
        with patch("src.middleware.security.settings") as ms:
            ms.auth_enabled = False
            ms.master_api_key = "master-secret-32chars-aaaaaaaaaa"
            response = client.get("/api/v1/admin/stats?hours=1")
            assert response.status_code in (401, 403), response.text


class TestAuthEnabledDefaultUnchanged:
    def test_default_settings_keep_auth_required(self, client, mock_services):
        """Sanity: with AUTH_ENABLED untouched, requests without a key still 401."""
        response = client.get("/files/some-session-id")
        assert response.status_code == 401, response.text
