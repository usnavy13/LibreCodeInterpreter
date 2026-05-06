"""Integration tests for HTTP Basic auth in URL credentials.

LibreChat (since librechat-agents commit dd3de99, April 2026) no longer sends
the X-API-Key header. Operators wanting per-client auth point LibreChat at
`https://KEY@your-api/v1` — axios/node-fetch automatically generate
`Authorization: Basic base64(KEY:)`. These tests verify our server accepts
that pattern, with x-api-key still taking precedence when both are present.
"""

import base64
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.main import app

VALID_KEY = "test-api-key-for-testing-12345"


def _basic_header(token_pair: str) -> str:
    return "Basic " + base64.b64encode(token_pair.encode()).decode()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_services():
    """Mock service deps so the request gets past handler stage; we only care about auth."""
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


class TestBasicAuthAccepted:
    def test_valid_basic_auth_passes_authentication(self, client, mock_services):
        """LibreChat-style URL credentials => Authorization: Basic base64(KEY:)."""
        with patch("src.services.auth.settings") as mock_settings:
            mock_settings.api_key = VALID_KEY
            response = client.get(
                "/files/some-session-id",
                headers={"authorization": _basic_header(f"{VALID_KEY}:")},
            )
            assert response.status_code != 401, response.text

    def test_basic_auth_via_testclient_auth_param(self, client, mock_services):
        """End-to-end: TestClient.auth=(KEY, '') generates the same header axios would."""
        with patch("src.services.auth.settings") as mock_settings:
            mock_settings.api_key = VALID_KEY
            response = client.get("/files/some-session-id", auth=(VALID_KEY, ""))
            assert response.status_code != 401, response.text

    def test_invalid_basic_auth_rejected(self, client, mock_services):
        with patch("src.services.auth.settings") as mock_settings:
            mock_settings.api_key = VALID_KEY
            response = client.get(
                "/files/some-session-id",
                headers={"authorization": _basic_header("wrong-key:")},
            )
            assert response.status_code == 401

    def test_basic_auth_with_password_field_uses_username(self, client, mock_services):
        """Conventionally the key is the username; password is empty. Verify username wins."""
        with patch("src.services.auth.settings") as mock_settings:
            mock_settings.api_key = VALID_KEY
            response = client.get(
                "/files/some-session-id",
                headers={
                    "authorization": _basic_header(f"{VALID_KEY}:ignored-password")
                },
            )
            assert response.status_code != 401


class TestPrecedence:
    def test_x_api_key_wins_when_both_present(self, client, mock_services):
        """If both headers present, x-api-key is used (deterministic for proxy setups)."""
        with patch("src.services.auth.settings") as mock_settings:
            mock_settings.api_key = VALID_KEY
            response = client.get(
                "/files/some-session-id",
                headers={
                    "x-api-key": VALID_KEY,
                    "authorization": _basic_header("wrong-key:"),
                },
            )
            assert response.status_code != 401

    def test_invalid_x_api_key_does_not_fall_back_to_basic(self, client, mock_services):
        """If x-api-key is present but invalid, we reject — no quiet Basic fallback."""
        with patch("src.services.auth.settings") as mock_settings:
            mock_settings.api_key = VALID_KEY
            response = client.get(
                "/files/some-session-id",
                headers={
                    "x-api-key": "wrong-key",
                    "authorization": _basic_header(f"{VALID_KEY}:"),
                },
            )
            assert response.status_code == 401


class TestNonBasicSchemesRejected:
    def test_bearer_still_rejected(self, client, mock_services):
        response = client.get(
            "/files/some-session-id",
            headers={"authorization": f"Bearer {VALID_KEY}"},
        )
        assert response.status_code == 401

    def test_apikey_scheme_still_rejected(self, client, mock_services):
        response = client.get(
            "/files/some-session-id",
            headers={"authorization": f"ApiKey {VALID_KEY}"},
        )
        assert response.status_code == 401

    def test_malformed_basic_auth_rejected(self, client, mock_services):
        response = client.get(
            "/files/some-session-id",
            headers={"authorization": "Basic !!!not-base64!!!"},
        )
        assert response.status_code == 401
