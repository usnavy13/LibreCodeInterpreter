"""Async HTTP client for load testing the Code Interpreter API."""

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import aiohttp

from .models import ExecutionResult


class LoadTestClient:
    """Async HTTP client for load testing."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_seconds: int = 60,
        max_connections: int = 100,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.max_connections = max_connections
        self._session: Optional[aiohttp.ClientSession] = None
        self._request_counter = 0

    async def __aenter__(self) -> "LoadTestClient":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def start(self) -> None:
        """Initialize the HTTP session."""
        if self._session is None:
            connector = aiohttp.TCPConnector(
                limit=self.max_connections,
                ssl=False,  # Allow self-signed certs
            )
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=self.timeout,
            )

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None

    def _get_headers(self) -> Dict[str, str]:
        """Get request headers."""
        return {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
        }

    async def execute_code(
        self,
        code: str,
        language: str = "py",
        scenario_id: str = "unknown",
        session_id: Optional[str] = None,
        entity_id: Optional[str] = None,
    ) -> ExecutionResult:
        """Execute code on the API and return result."""
        if self._session is None:
            await self.start()

        self._request_counter += 1
        request_id = f"load-test-{self._request_counter}"

        payload = {
            "lang": language,
            "code": code,
            "entity_id": entity_id or request_id,
            "user_id": "load-tester",
        }
        if session_id:
            payload["session_id"] = session_id

        start_time = time.perf_counter()
        try:
            async with self._session.post(
                f"{self.base_url}/exec",
                json=payload,
                headers=self._get_headers(),
                ssl=False,
            ) as response:
                latency_ms = (time.perf_counter() - start_time) * 1000
                body = await response.json()

                success = response.status == 200
                error = None
                if not success:
                    error = body.get("detail", f"HTTP {response.status}")

                # Extract container source if available
                container_source = None
                if "container_source" in body:
                    container_source = body["container_source"]

                return ExecutionResult(
                    success=success,
                    latency_ms=latency_ms,
                    status_code=response.status,
                    language=language,
                    scenario_id=scenario_id,
                    error=error,
                    files_generated=len(body.get("files", [])),
                    timestamp=datetime.now(timezone.utc),
                    container_source=container_source,
                )

        except asyncio.TimeoutError:
            latency_ms = (time.perf_counter() - start_time) * 1000
            return ExecutionResult(
                success=False,
                latency_ms=latency_ms,
                status_code=0,
                language=language,
                scenario_id=scenario_id,
                error="Request timed out",
                timestamp=datetime.now(timezone.utc),
            )
        except aiohttp.ClientError as e:
            latency_ms = (time.perf_counter() - start_time) * 1000
            return ExecutionResult(
                success=False,
                latency_ms=latency_ms,
                status_code=0,
                language=language,
                scenario_id=scenario_id,
                error=str(e),
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            latency_ms = (time.perf_counter() - start_time) * 1000
            return ExecutionResult(
                success=False,
                latency_ms=latency_ms,
                status_code=0,
                language=language,
                scenario_id=scenario_id,
                error=f"Unexpected error: {e}",
                timestamp=datetime.now(timezone.utc),
            )

    async def check_health(self) -> Dict[str, Any]:
        """Check API health status."""
        if self._session is None:
            await self.start()

        try:
            async with self._session.get(
                f"{self.base_url}/health",
                ssl=False,
            ) as response:
                if response.status == 200:
                    return await response.json()
                return {"status": "unhealthy", "code": response.status}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def check_detailed_health(self) -> Dict[str, Any]:
        """Check detailed API health status."""
        if self._session is None:
            await self.start()

        try:
            async with self._session.get(
                f"{self.base_url}/health/detailed",
                headers=self._get_headers(),
                ssl=False,
            ) as response:
                if response.status == 200:
                    return await response.json()
                return {"status": "unhealthy", "code": response.status}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_metrics(self) -> Dict[str, Any]:
        """Get API metrics."""
        if self._session is None:
            await self.start()

        try:
            async with self._session.get(
                f"{self.base_url}/metrics",
                headers=self._get_headers(),
                ssl=False,
            ) as response:
                if response.status == 200:
                    return await response.json()
                return {"error": f"HTTP {response.status}"}
        except Exception as e:
            return {"error": str(e)}

    async def get_pool_metrics(self) -> Dict[str, Any]:
        """Get sandbox pool metrics."""
        if self._session is None:
            await self.start()

        try:
            async with self._session.get(
                f"{self.base_url}/metrics/pool",
                headers=self._get_headers(),
                ssl=False,
            ) as response:
                if response.status == 200:
                    return await response.json()
                return {"error": f"HTTP {response.status}"}
        except Exception as e:
            return {"error": str(e)}

    async def warmup(self, language: str = "py", count: int = 5) -> int:
        """Warm up the API with a few requests."""
        code = "print('warmup')"
        successful = 0
        for _ in range(count):
            result = await self.execute_code(code, language, "warmup")
            if result.success:
                successful += 1
        return successful
