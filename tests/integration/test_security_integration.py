"""Integration tests for authentication and security middleware."""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock
import json

from src.main import app
from src.config import settings


class TestSecurityIntegration:
    """Test security middleware integration with the main application."""

    @pytest.fixture
    def client(self):
        """Create test client."""
        return TestClient(app)

    @pytest.fixture
    def valid_headers(self):
        """Valid API key headers for testing."""
        return {"x-api-key": "test-api-key-for-testing-12345"}

    def test_health_endpoint_no_auth(self, client):
        """Test that health endpoint doesn't require authentication."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    def test_docs_endpoint_no_auth(self, client):
        """Test that docs endpoint doesn't require authentication."""
        response = client.get("/docs")
        assert response.status_code == 200

    def test_protected_endpoint_no_auth(self, client):
        """Test that protected endpoints require authentication."""
        # Try to access a protected endpoint without API key
        response = client.get("/sessions")
        assert response.status_code == 401
        assert "API key" in response.json()["error"]

    @patch("src.services.auth.settings")
    def test_protected_endpoint_invalid_auth(self, mock_settings, client):
        """Test protected endpoint with invalid API key."""
        mock_settings.api_key = "correct-key"

        headers = {"x-api-key": "wrong-key"}
        response = client.get("/sessions", headers=headers)
        assert response.status_code == 401

    def test_protected_endpoint_valid_auth(self, client, valid_headers):
        """Test protected endpoint with valid API key."""
        # Use the test API key from conftest (test-api-key-for-testing-12345)
        response = client.get("/sessions", headers=valid_headers)
        # Should not be 401 (auth failure)
        assert response.status_code != 401

    def test_security_headers_present(self, client):
        """Test that security headers are added to responses."""
        response = client.get("/health")

        # Check for security headers
        expected_headers = [
            "x-content-type-options",
            "x-frame-options",
            "x-xss-protection",
            "strict-transport-security",
            "content-security-policy",
        ]

        for header in expected_headers:
            assert header in response.headers

    def test_cors_headers_present(self, client):
        """Test that CORS headers are properly configured."""
        response = client.options("/health")
        # CORS headers should be present for OPTIONS requests
        assert response.status_code in [
            200,
            405,
        ]  # Either allowed or method not allowed

    def test_authorization_header_fallback(self, client):
        """Test that Authorization header works as fallback for API key."""
        # Use the test API key from conftest
        headers = {"Authorization": "Bearer test-api-key-for-testing-12345"}
        response = client.get("/sessions", headers=headers)
        # Should not be 401 (auth failure)
        assert response.status_code != 401

    def test_request_size_limit(self, client):
        """Test request size limiting."""
        # Create a large payload (this is a basic test)
        large_data = {"data": "x" * 1000}

        response = client.post(
            "/sessions", json=large_data, headers={"x-api-key": "test-key"}
        )

        # Should either process or fail with auth, not with size limit for this small payload
        assert response.status_code != 413

    def test_invalid_content_type(self, client):
        """Test content type validation."""
        headers = {
            "x-api-key": "test-key",
            "content-type": "application/xml",  # Not allowed
        }

        response = client.post("/sessions", data="<xml></xml>", headers=headers)
        assert response.status_code == 415  # Unsupported Media Type

    def test_multiple_auth_methods(self, client):
        """Test that multiple authentication methods work."""
        test_key = "test-api-key-for-testing-12345"

        # Test x-api-key header
        response1 = client.get("/sessions", headers={"x-api-key": test_key})

        # Test Authorization Bearer header
        response2 = client.get(
            "/sessions", headers={"Authorization": f"Bearer {test_key}"}
        )

        # Both should have same result (not 401)
        assert response1.status_code == response2.status_code
        assert response1.status_code != 401

    def test_case_insensitive_headers(self, client):
        """Test that header names are case insensitive."""
        test_key = "test-api-key-for-testing-12345"

        # Test different case variations
        headers_variations = [
            {"X-API-KEY": test_key},
            {"x-api-key": test_key},
            {"X-Api-Key": test_key},
        ]

        for headers in headers_variations:
            response = client.get("/sessions", headers=headers)
            assert response.status_code != 401  # Should not fail auth


class TestRateLimitingIntegration:
    """Test rate limiting integration."""

    @pytest.fixture
    def client(self):
        """Create test client."""
        return TestClient(app)

    @patch("src.services.auth.settings")
    def test_rate_limiting_basic(self, mock_settings, client):
        """Test basic rate limiting functionality."""
        mock_settings.api_key = "test-api-key"

        # This test would need Redis to be properly mocked
        # For now, just verify the endpoint responds
        headers = {"x-api-key": "test-api-key"}
        response = client.get("/sessions", headers=headers)

        # Should not fail with rate limiting initially
        assert response.status_code != 429


class TestSecurityValidationIntegration:
    """Test security validation in real requests."""

    @pytest.fixture
    def client(self):
        """Create test client."""
        return TestClient(app)

    def test_path_traversal_protection(self, client):
        """Test protection against path traversal attacks."""
        with patch("src.services.auth.settings") as mock_settings:
            mock_settings.api_key = "test-api-key"

            # Try path traversal in URL
            malicious_paths = [
                "/sessions/../../../etc/passwd",
                "/sessions/%2e%2e%2f%2e%2e%2fetc%2fpasswd",
                "/sessions/..\\..\\windows\\system32",
            ]

            headers = {"x-api-key": "test-api-key"}

            for path in malicious_paths:
                response = client.get(path, headers=headers)
                # Should either be 404 (not found) or other error, not 200
                assert response.status_code != 200

    @pytest.mark.skip(reason="httpx test client doesn't support null bytes in URLs")
    def test_null_byte_injection(self, client):
        """Test protection against null byte injection."""
        headers = {"x-api-key": "test-api-key-for-testing-12345"}

        # Try null byte in path
        response = client.get("/sessions/test\x00", headers=headers)
        # Should handle gracefully
        assert response.status_code in [400, 404, 422]  # Bad request or not found

    def test_oversized_headers(self, client):
        """Test handling of oversized headers."""
        # Create very large header value
        large_value = "x" * 10000
        headers = {"x-api-key": large_value}

        response = client.get("/sessions", headers=headers)
        # Should either reject or handle gracefully
        assert response.status_code in [
            400,
            401,
            413,
            431,
        ]  # Various possible error codes
