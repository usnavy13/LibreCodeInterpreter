"""Health check service for monitoring system dependencies."""

# Standard library imports
import asyncio
import os
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Any, Optional

# Third-party imports
import docker
import redis.asyncio as redis
import structlog
from minio import Minio
from minio.error import S3Error

# Local application imports
from ..config import settings


logger = structlog.get_logger(__name__)

# Check if running in Azure deployment mode
IS_AZURE_DEPLOYMENT = os.environ.get("DEPLOYMENT_MODE", "docker").lower() == "azure"


class HealthStatus(str, Enum):
    """Health check status enumeration."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class HealthCheckResult:
    """Health check result container."""

    def __init__(
        self,
        service: str,
        status: HealthStatus,
        response_time_ms: Optional[float] = None,
        details: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ):
        self.service = service
        self.status = status
        self.response_time_ms = response_time_ms
        self.details = details or {}
        self.error = error
        self.timestamp = datetime.now(timezone.utc)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            "service": self.service,
            "status": self.status.value,
            "timestamp": self.timestamp.isoformat(),
        }

        if self.response_time_ms is not None:
            result["response_time_ms"] = round(self.response_time_ms, 2)

        if self.details:
            result["details"] = self.details

        if self.error:
            result["error"] = self.error

        return result


class HealthCheckService:
    """Service for performing health checks on system dependencies."""

    def __init__(self):
        """Initialize health check service."""
        self._redis_client: Optional[redis.Redis] = None
        self._docker_client: Optional[docker.DockerClient] = None
        self._minio_client: Optional[Minio] = None
        self._container_pool = None
        self._last_check_time: Optional[datetime] = None
        self._cached_results: Dict[str, HealthCheckResult] = {}
        self._cache_ttl_seconds = 30  # Cache results for 30 seconds

    def set_container_pool(self, pool) -> None:
        """Set container pool reference for health checks."""
        self._container_pool = pool

    async def check_all_services(
        self, use_cache: bool = True
    ) -> Dict[str, HealthCheckResult]:
        """Perform health checks on all services."""
        now = datetime.now(timezone.utc)

        # Check if we can use cached results
        if (
            use_cache
            and self._last_check_time
            and (now - self._last_check_time).total_seconds() < self._cache_ttl_seconds
        ):
            return self._cached_results

        logger.info("Performing health checks on all services")

        # Run all health checks concurrently
        # In Azure mode, skip Docker and MinIO checks (use Azure services instead)
        if IS_AZURE_DEPLOYMENT:
            tasks = [
                self.check_redis(),
                self.check_azure_storage(),
                self.check_executor_service(),
            ]
            service_names = ["redis", "azure_storage", "executor"]
        else:
            tasks = [
                self.check_redis(),
                self.check_minio(),
                self.check_docker(),
            ]
            service_names = ["redis", "minio", "docker"]

            # Add container pool check if pool is configured (Docker mode only)
            if self._container_pool and settings.container_pool_enabled:
                tasks.append(self.check_container_pool())
                service_names.append("container_pool")

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        health_results = {}

        for i, result in enumerate(results):
            service_name = service_names[i]
            if isinstance(result, Exception):
                logger.error(
                    f"Health check failed for {service_name}", error=str(result)
                )
                health_results[service_name] = HealthCheckResult(
                    service=service_name,
                    status=HealthStatus.UNHEALTHY,
                    error=str(result),
                )
            else:
                health_results[service_name] = result

        # Cache results
        self._cached_results = health_results
        self._last_check_time = now

        return health_results

    async def check_redis(self) -> HealthCheckResult:
        """Check Redis connectivity and performance."""
        start_time = time.time()

        try:
            # Use shared connection pool
            if not self._redis_client:
                from ..core.pool import redis_pool

                self._redis_client = redis_pool.get_client()

            # Test basic connectivity
            await self._redis_client.ping()

            # Test read/write operations
            test_key = "health_check:test"
            test_value = f"test_{int(time.time())}"

            await self._redis_client.set(test_key, test_value, ex=60)
            retrieved_value = await self._redis_client.get(test_key)
            await self._redis_client.delete(test_key)

            if retrieved_value != test_value:
                raise Exception("Redis read/write test failed")

            # Get Redis info
            info = await self._redis_client.info()

            response_time = (time.time() - start_time) * 1000

            # Determine status based on response time and memory usage
            status = HealthStatus.HEALTHY
            if response_time > 1000:  # > 1 second
                status = HealthStatus.DEGRADED

            memory_usage_mb = info.get("used_memory", 0) / (1024 * 1024)
            max_memory_mb = (
                info.get("maxmemory", 0) / (1024 * 1024)
                if info.get("maxmemory", 0) > 0
                else None
            )

            details = {
                "version": info.get("redis_version", "unknown"),
                "connected_clients": info.get("connected_clients", 0),
                "memory_usage_mb": round(memory_usage_mb, 2),
                "keyspace_hits": info.get("keyspace_hits", 0),
                "keyspace_misses": info.get("keyspace_misses", 0),
                "uptime_seconds": info.get("uptime_in_seconds", 0),
            }

            if max_memory_mb:
                details["max_memory_mb"] = round(max_memory_mb, 2)
                details["memory_usage_percent"] = round(
                    (memory_usage_mb / max_memory_mb) * 100, 2
                )

            return HealthCheckResult(
                service="redis",
                status=status,
                response_time_ms=response_time,
                details=details,
            )

        except Exception as e:
            response_time = (time.time() - start_time) * 1000
            logger.error(
                "Redis health check failed",
                error=str(e),
                response_time_ms=response_time,
            )

            return HealthCheckResult(
                service="redis",
                status=HealthStatus.UNHEALTHY,
                response_time_ms=response_time,
                error=str(e),
            )

    async def check_minio(self) -> HealthCheckResult:
        """Check MinIO/S3 connectivity and performance."""
        start_time = time.time()

        try:
            # Create MinIO client if not exists
            if not self._minio_client:
                self._minio_client = Minio(
                    settings.minio_endpoint,
                    access_key=settings.minio_access_key,
                    secret_key=settings.minio_secret_key,
                    secure=settings.minio_secure,
                )

            # Test basic connectivity by listing buckets
            loop = asyncio.get_event_loop()
            buckets = await loop.run_in_executor(None, self._minio_client.list_buckets)

            # Check if our bucket exists
            bucket_exists = await loop.run_in_executor(
                None, self._minio_client.bucket_exists, settings.minio_bucket
            )

            if not bucket_exists:
                # Try to create the bucket
                await loop.run_in_executor(
                    None, self._minio_client.make_bucket, settings.minio_bucket
                )
                logger.info(f"Created missing bucket: {settings.minio_bucket}")

            # Test read/write operations
            test_object = f"health_check/test_{int(time.time())}.txt"
            test_content = b"health check test content"

            # Create a BytesIO object for the upload
            from io import BytesIO

            test_data = BytesIO(test_content)

            # Upload test object
            await loop.run_in_executor(
                None,
                self._minio_client.put_object,
                settings.minio_bucket,
                test_object,
                test_data,
                len(test_content),
            )

            # Download test object
            response = await loop.run_in_executor(
                None, self._minio_client.get_object, settings.minio_bucket, test_object
            )

            downloaded_content = response.read()
            response.close()
            response.release_conn()

            # Clean up test object
            await loop.run_in_executor(
                None,
                self._minio_client.remove_object,
                settings.minio_bucket,
                test_object,
            )

            if downloaded_content != test_content:
                raise Exception("MinIO read/write test failed")

            response_time = (time.time() - start_time) * 1000

            # Determine status based on response time
            status = HealthStatus.HEALTHY
            if response_time > 2000:  # > 2 seconds
                status = HealthStatus.DEGRADED

            details = {
                "endpoint": settings.minio_endpoint,
                "bucket": settings.minio_bucket,
                "bucket_exists": bucket_exists,
                "total_buckets": len(buckets),
                "secure": settings.minio_secure,
            }

            return HealthCheckResult(
                service="minio",
                status=status,
                response_time_ms=response_time,
                details=details,
            )

        except S3Error as e:
            response_time = (time.time() - start_time) * 1000
            logger.error(
                "MinIO health check failed",
                error=str(e),
                response_time_ms=response_time,
            )

            return HealthCheckResult(
                service="minio",
                status=HealthStatus.UNHEALTHY,
                response_time_ms=response_time,
                error=f"S3 Error: {e.message if hasattr(e, 'message') else str(e)}",
            )

        except Exception as e:
            response_time = (time.time() - start_time) * 1000
            logger.error(
                "MinIO health check failed",
                error=str(e),
                response_time_ms=response_time,
            )

            return HealthCheckResult(
                service="minio",
                status=HealthStatus.UNHEALTHY,
                response_time_ms=response_time,
                error=str(e),
            )

    async def check_docker(self) -> HealthCheckResult:
        """Check Docker daemon connectivity and performance."""
        start_time = time.time()

        try:
            # Create Docker client if not exists
            if not self._docker_client:
                try:
                    # Try to use the default Docker socket
                    self._docker_client = docker.from_env(
                        timeout=settings.health_check_timeout
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to create Docker client from environment: {e}"
                    )
                    # Fallback to explicit socket path
                    self._docker_client = docker.DockerClient(
                        base_url="unix://var/run/docker.sock",
                        timeout=settings.health_check_timeout,
                    )

            # Test basic connectivity
            loop = asyncio.get_event_loop()
            version_info = await loop.run_in_executor(None, self._docker_client.version)

            # Get system info
            system_info = await loop.run_in_executor(None, self._docker_client.info)

            # List containers to test API functionality
            containers = await loop.run_in_executor(
                None, self._docker_client.containers.list, True
            )

            # Check if we can pull a simple image (test registry connectivity)
            try:
                await loop.run_in_executor(
                    None, self._docker_client.images.pull, "hello-world:latest"
                )
                registry_accessible = True
            except Exception as e:
                logger.warning("Docker registry not accessible", error=str(e))
                registry_accessible = False

            response_time = (time.time() - start_time) * 1000

            # Determine status
            status = HealthStatus.HEALTHY
            if response_time > 3000:  # > 3 seconds
                status = HealthStatus.DEGRADED
            elif not registry_accessible:
                status = HealthStatus.DEGRADED

            # Calculate resource usage
            total_containers = len(containers)
            running_containers = len([c for c in containers if c.status == "running"])

            details = {
                "version": version_info.get("Version", "unknown"),
                "api_version": version_info.get("ApiVersion", "unknown"),
                "platform": version_info.get("Platform", {}).get("Name", "unknown"),
                "total_containers": total_containers,
                "running_containers": running_containers,
                "registry_accessible": registry_accessible,
                "server_version": system_info.get("ServerVersion", "unknown"),
                "memory_total_gb": round(system_info.get("MemTotal", 0) / (1024**3), 2),
                "cpu_count": system_info.get("NCPU", 0),
            }

            return HealthCheckResult(
                service="docker",
                status=status,
                response_time_ms=response_time,
                details=details,
            )

        except Exception as e:
            response_time = (time.time() - start_time) * 1000
            logger.error(
                "Docker health check failed",
                error=str(e),
                response_time_ms=response_time,
            )

            return HealthCheckResult(
                service="docker",
                status=HealthStatus.UNHEALTHY,
                response_time_ms=response_time,
                error=str(e),
            )

    async def check_container_pool(self) -> HealthCheckResult:
        """Check container pool health and statistics."""
        start_time = time.time()

        try:
            if not self._container_pool:
                return HealthCheckResult(
                    service="container_pool",
                    status=HealthStatus.UNKNOWN,
                    error="Container pool not configured",
                )

            # Get pool statistics
            stats = self._container_pool.get_stats()

            response_time = (time.time() - start_time) * 1000

            # Calculate totals
            total_available = sum(s.available_count for s in stats.values())
            total_acquisitions = sum(s.total_acquisitions for s in stats.values())
            pool_hits = sum(s.pool_hits for s in stats.values())
            pool_misses = sum(s.pool_misses for s in stats.values())

            # Calculate hit rate (pool hits / total acquisitions)
            hit_rate = 0.0
            if total_acquisitions > 0:
                hit_rate = (pool_hits / total_acquisitions) * 100

            # Determine status
            status = HealthStatus.HEALTHY
            if total_available == 0:
                status = HealthStatus.DEGRADED  # Pool is empty
            elif hit_rate < 50 and total_acquisitions > 10:
                status = HealthStatus.DEGRADED  # Low hit rate

            # Per-language breakdown
            language_stats = {}
            for lang, s in stats.items():
                language_stats[lang] = {
                    "available": s.available_count,
                    "acquisitions": s.total_acquisitions,
                    "pool_hits": s.pool_hits,
                    "pool_misses": s.pool_misses,
                }

            details = {
                "enabled": True,
                "architecture": "stateless",  # Containers destroyed after each execution
                "total_available": total_available,
                "total_acquisitions": total_acquisitions,
                "pool_hits": pool_hits,
                "pool_misses": pool_misses,
                "hit_rate_percent": round(hit_rate, 2),
                "languages": language_stats,
            }

            return HealthCheckResult(
                service="container_pool",
                status=status,
                response_time_ms=response_time,
                details=details,
            )

        except Exception as e:
            response_time = (time.time() - start_time) * 1000
            logger.error("Container pool health check failed", error=str(e))

            return HealthCheckResult(
                service="container_pool",
                status=HealthStatus.UNHEALTHY,
                response_time_ms=response_time,
                error=str(e),
            )

    async def check_azure_storage(self) -> HealthCheckResult:
        """Check Azure Blob Storage connectivity (Azure deployment mode only)."""
        start_time = time.time()

        try:
            from ..config.azure import azure_settings
            from azure.storage.blob import BlobServiceClient

            if not azure_settings.azure_storage_connection_string:
                return HealthCheckResult(
                    service="azure_storage",
                    status=HealthStatus.UNKNOWN,
                    error="Azure Storage not configured",
                )

            # Create client and test connectivity
            loop = asyncio.get_event_loop()
            blob_service = BlobServiceClient.from_connection_string(
                azure_settings.azure_storage_connection_string
            )

            # Test connectivity by getting service properties
            await loop.run_in_executor(
                None, blob_service.get_service_properties
            )

            # Check if our container exists
            container_client = blob_service.get_container_client(
                azure_settings.azure_storage_container
            )
            container_exists = await loop.run_in_executor(
                None, container_client.exists
            )

            response_time = (time.time() - start_time) * 1000

            status = HealthStatus.HEALTHY
            if response_time > 2000:
                status = HealthStatus.DEGRADED

            details = {
                "container": azure_settings.azure_storage_container,
                "container_exists": container_exists,
            }

            return HealthCheckResult(
                service="azure_storage",
                status=status,
                response_time_ms=response_time,
                details=details,
            )

        except Exception as e:
            response_time = (time.time() - start_time) * 1000
            logger.error(
                "Azure Storage health check failed",
                error=str(e),
                response_time_ms=response_time,
            )

            return HealthCheckResult(
                service="azure_storage",
                status=HealthStatus.UNHEALTHY,
                response_time_ms=response_time,
                error=str(e),
            )

    async def check_executor_service(self) -> HealthCheckResult:
        """Check executor service connectivity (Azure deployment mode only)."""
        start_time = time.time()

        try:
            from ..config.azure import azure_settings
            import httpx

            if not azure_settings.executor_url:
                return HealthCheckResult(
                    service="executor",
                    status=HealthStatus.UNKNOWN,
                    error="Executor URL not configured",
                )

            # Check executor health endpoint
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{azure_settings.executor_url}/health")

            response_time = (time.time() - start_time) * 1000

            if response.status_code == 200:
                status = HealthStatus.HEALTHY
                if response_time > 1000:
                    status = HealthStatus.DEGRADED

                details = {
                    "url": azure_settings.executor_url,
                    "status_code": response.status_code,
                }

                # Try to parse response for more details
                try:
                    health_data = response.json()
                    details.update(health_data)
                except Exception:
                    pass

                return HealthCheckResult(
                    service="executor",
                    status=status,
                    response_time_ms=response_time,
                    details=details,
                )
            else:
                return HealthCheckResult(
                    service="executor",
                    status=HealthStatus.UNHEALTHY,
                    response_time_ms=response_time,
                    error=f"Executor returned status {response.status_code}",
                )

        except Exception as e:
            response_time = (time.time() - start_time) * 1000
            logger.error(
                "Executor health check failed",
                error=str(e),
                response_time_ms=response_time,
            )

            return HealthCheckResult(
                service="executor",
                status=HealthStatus.UNHEALTHY,
                response_time_ms=response_time,
                error=str(e),
            )

    def get_overall_status(
        self, service_results: Dict[str, HealthCheckResult]
    ) -> HealthStatus:
        """Determine overall system health status."""
        if not service_results:
            return HealthStatus.UNKNOWN

        statuses = [result.status for result in service_results.values()]

        # If any service is unhealthy, overall status is unhealthy
        if HealthStatus.UNHEALTHY in statuses:
            return HealthStatus.UNHEALTHY

        # If any service is degraded, overall status is degraded
        if HealthStatus.DEGRADED in statuses:
            return HealthStatus.DEGRADED

        # If all services are healthy, overall status is healthy
        if all(status == HealthStatus.HEALTHY for status in statuses):
            return HealthStatus.HEALTHY

        return HealthStatus.UNKNOWN

    async def close(self) -> None:
        """Close all client connections."""
        try:
            # Close Redis connection with timeout
            if self._redis_client:
                try:
                    await asyncio.wait_for(self._redis_client.close(), timeout=2.0)
                except asyncio.TimeoutError:
                    logger.warning("Redis connection close timed out during shutdown")
                except Exception as e:
                    logger.warning(
                        f"Error closing Redis connection during shutdown: {e}"
                    )

            # Close Docker connection with timeout
            if self._docker_client:
                try:
                    # Docker client close is synchronous, but wrap in executor with timeout
                    loop = asyncio.get_event_loop()
                    await asyncio.wait_for(
                        loop.run_in_executor(None, self._docker_client.close),
                        timeout=2.0,
                    )
                except asyncio.TimeoutError:
                    logger.warning("Docker connection close timed out during shutdown")
                except Exception as e:
                    logger.warning(
                        f"Error closing Docker connection during shutdown: {e}"
                    )

            logger.info("Closed health check service connections")

        except Exception as e:
            logger.error("Error closing health check service connections", error=str(e))


# Global health check service instance
health_service = HealthCheckService()
