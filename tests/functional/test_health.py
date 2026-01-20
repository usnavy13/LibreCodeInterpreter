"""Functional tests for health check endpoints."""

import time

import pytest


class TestBasicHealth:
    """Tests for GET /health (no auth required)."""

    @pytest.mark.asyncio
    async def test_health_returns_200(self, async_client):
        """Health endpoint returns 200 OK."""
        response = await async_client.get("/health")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_health_under_1s(self, async_client):
        """Health check responds within 1 second."""
        start = time.perf_counter()
        response = await async_client.get("/health")
        latency = time.perf_counter() - start

        assert response.status_code == 200
        assert latency < 1.0, f"Health check took {latency:.2f}s, expected < 1s"

    @pytest.mark.asyncio
    async def test_health_response_fields(self, async_client):
        """Health response has expected fields."""
        response = await async_client.get("/health")
        data = response.json()

        # Required fields
        assert "status" in data, "Missing field: status"
        assert "version" in data, "Missing field: version"
        assert data["status"] == "healthy"


class TestDetailedHealth:
    """Tests for GET /health/detailed (requires auth)."""

    @pytest.mark.asyncio
    async def test_detailed_health_requires_auth(self, async_client):
        """Detailed health check requires API key."""
        response = await async_client.get("/health/detailed")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_detailed_health_with_auth(self, async_client, auth_headers):
        """Detailed health check returns service status."""
        response = await async_client.get("/health/detailed", headers=auth_headers)

        # May be 200 (healthy) or 503 (degraded/unhealthy)
        assert response.status_code in [200, 503]
        data = response.json()

        assert "status" in data
        assert "services" in data
        assert "summary" in data
