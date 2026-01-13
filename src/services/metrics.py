"""Metrics collection service for monitoring API usage and performance."""

# Standard library imports
import asyncio
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Any, Optional, List

# Third-party imports
import redis.asyncio as redis
import structlog

# Local application imports
from ..config import settings


logger = structlog.get_logger(__name__)


class MetricType(str, Enum):
    """Metric type enumeration."""

    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    TIMER = "timer"


@dataclass
class MetricPoint:
    """Individual metric data point."""

    name: str
    value: float
    timestamp: datetime
    labels: Dict[str, str] = field(default_factory=dict)
    metric_type: MetricType = MetricType.GAUGE


@dataclass
class ExecutionMetrics:
    """Execution-specific metrics."""

    execution_id: str
    session_id: str
    language: str
    status: str
    execution_time_ms: float
    memory_peak_mb: Optional[float] = None
    cpu_time_ms: Optional[float] = None
    exit_code: Optional[int] = None
    file_count: int = 0
    output_size_bytes: int = 0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class APIMetrics:
    """API request metrics."""

    endpoint: str
    method: str
    status_code: int
    response_time_ms: float
    request_size_bytes: int = 0
    response_size_bytes: int = 0
    user_agent: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class MetricsCollector:
    """In-memory metrics collector with Redis persistence."""

    def __init__(self):
        """Initialize metrics collector."""
        self._redis_client: Optional[redis.Redis] = None
        self._metrics_buffer: deque = deque(maxlen=10000)  # Buffer for recent metrics
        self._counters: Dict[str, float] = defaultdict(float)
        self._gauges: Dict[str, float] = {}
        self._histograms: Dict[str, List[float]] = defaultdict(list)
        self._timers: Dict[str, List[float]] = defaultdict(list)

        # Aggregated statistics
        self._execution_stats = {
            "total_executions": 0,
            "successful_executions": 0,
            "failed_executions": 0,
            "timeout_executions": 0,
            "total_execution_time_ms": 0,
            "total_memory_usage_mb": 0,
            "language_counts": defaultdict(int),
            "hourly_executions": defaultdict(int),
        }

        self._api_stats = {
            "total_requests": 0,
            "successful_requests": 0,
            "error_requests": 0,
            "total_response_time_ms": 0,
            "endpoint_counts": defaultdict(int),
            "status_code_counts": defaultdict(int),
            "hourly_requests": defaultdict(int),
        }

        # Background task for metrics persistence
        self._persistence_task: Optional[asyncio.Task] = None
        self._persistence_interval = 60  # Persist metrics every 60 seconds

    async def start(self) -> None:
        """Start the metrics collector."""
        try:
            # Use shared connection pool
            from ..core.pool import redis_pool

            self._redis_client = redis_pool.get_client()

            # Test Redis connection with timeout
            await asyncio.wait_for(self._redis_client.ping(), timeout=3.0)  # type: ignore[arg-type]

            # Load existing metrics from Redis
            await self._load_metrics_from_redis()

            # Start background persistence task
            self._persistence_task = asyncio.create_task(self._persistence_loop())

            logger.info("Metrics collector started with Redis persistence")

        except asyncio.TimeoutError:
            logger.warning(
                "Redis connection timed out - metrics collector will run without persistence"
            )
            self._redis_client = None
        except Exception as e:
            logger.warning(
                "Failed to connect to Redis - metrics collector will run without persistence",
                error=str(e),
            )
            self._redis_client = None

        # Always start the metrics collector, even without Redis
        logger.info(
            "Metrics collector started (in-memory only)"
            if not self._redis_client
            else "Metrics collector started"
        )

    async def stop(self) -> None:
        """Stop the metrics collector."""
        try:
            # Stop persistence task
            if self._persistence_task and not self._persistence_task.done():
                self._persistence_task.cancel()
                try:
                    await asyncio.wait_for(self._persistence_task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    logger.info(
                        "Persistence task cancelled or timed out during shutdown"
                    )

            # Final metrics persistence with timeout to avoid hanging
            try:
                await asyncio.wait_for(self._persist_metrics_to_redis(), timeout=3.0)
            except asyncio.TimeoutError:
                logger.warning("Metrics persistence timed out during shutdown")
            except Exception as e:
                logger.warning(
                    "Failed to persist final metrics during shutdown", error=str(e)
                )

            # Close Redis connection
            if self._redis_client:
                try:
                    await asyncio.wait_for(self._redis_client.close(), timeout=1.0)
                except asyncio.TimeoutError:
                    logger.warning("Redis connection close timed out during shutdown")
                except Exception as e:
                    logger.warning(
                        f"Error closing Redis connection during shutdown: {e}"
                    )

            logger.info("Metrics collector stopped")

        except Exception as e:
            logger.error("Error stopping metrics collector", error=str(e))

    def record_execution_metrics(self, metrics: ExecutionMetrics) -> None:
        """Record code execution metrics."""
        try:
            # Add to buffer
            self._metrics_buffer.append(metrics)

            # Update counters
            self._counters["executions_total"] += 1
            self._counters[f"executions_by_language.{metrics.language}"] += 1
            self._counters[f"executions_by_status.{metrics.status}"] += 1

            # Update execution statistics
            self._execution_stats["total_executions"] += 1

            if metrics.status == "completed":
                self._execution_stats["successful_executions"] += 1
            elif metrics.status == "failed":
                self._execution_stats["failed_executions"] += 1
            elif metrics.status == "timeout":
                self._execution_stats["timeout_executions"] += 1

            self._execution_stats[
                "total_execution_time_ms"
            ] += metrics.execution_time_ms
            self._execution_stats["language_counts"][metrics.language] += 1

            if metrics.memory_peak_mb:
                self._execution_stats["total_memory_usage_mb"] += metrics.memory_peak_mb

            # Update hourly statistics
            hour_key = metrics.timestamp.strftime("%Y-%m-%d-%H")
            self._execution_stats["hourly_executions"][hour_key] += 1

            # Update histograms
            self._histograms["execution_time_ms"].append(metrics.execution_time_ms)
            if metrics.memory_peak_mb:
                self._histograms["memory_usage_mb"].append(metrics.memory_peak_mb)

            # Keep histogram size manageable
            if len(self._histograms["execution_time_ms"]) > 1000:
                self._histograms["execution_time_ms"] = self._histograms[
                    "execution_time_ms"
                ][-500:]
            if len(self._histograms["memory_usage_mb"]) > 1000:
                self._histograms["memory_usage_mb"] = self._histograms[
                    "memory_usage_mb"
                ][-500:]

            # Update gauges
            self._gauges["avg_execution_time_ms"] = (
                self._execution_stats["total_execution_time_ms"]
                / self._execution_stats["total_executions"]
            )

            if self._execution_stats["total_memory_usage_mb"] > 0:
                successful_with_memory = sum(
                    1
                    for m in self._metrics_buffer
                    if isinstance(m, ExecutionMetrics) and m.memory_peak_mb
                )
                if successful_with_memory > 0:
                    self._gauges["avg_memory_usage_mb"] = (
                        self._execution_stats["total_memory_usage_mb"]
                        / successful_with_memory
                    )

        except Exception as e:
            logger.error("Failed to record execution metrics", error=str(e))

    def record_api_metrics(self, metrics: APIMetrics) -> None:
        """Record API request metrics."""
        try:
            # Add to buffer
            self._metrics_buffer.append(metrics)

            # Update counters
            self._counters["api_requests_total"] += 1
            self._counters[f"api_requests_by_endpoint.{metrics.endpoint}"] += 1
            self._counters[f"api_requests_by_method.{metrics.method}"] += 1
            self._counters[f"api_requests_by_status.{metrics.status_code}"] += 1

            # Update API statistics
            self._api_stats["total_requests"] += 1

            if 200 <= metrics.status_code < 400:
                self._api_stats["successful_requests"] += 1
            else:
                self._api_stats["error_requests"] += 1

            self._api_stats["total_response_time_ms"] += metrics.response_time_ms
            self._api_stats["endpoint_counts"][metrics.endpoint] += 1
            self._api_stats["status_code_counts"][metrics.status_code] += 1

            # Update hourly statistics
            hour_key = metrics.timestamp.strftime("%Y-%m-%d-%H")
            self._api_stats["hourly_requests"][hour_key] += 1

            # Update histograms
            self._histograms["api_response_time_ms"].append(metrics.response_time_ms)

            # Keep histogram size manageable
            if len(self._histograms["api_response_time_ms"]) > 1000:
                self._histograms["api_response_time_ms"] = self._histograms[
                    "api_response_time_ms"
                ][-500:]

            # Update gauges
            self._gauges["avg_api_response_time_ms"] = (
                self._api_stats["total_response_time_ms"]
                / self._api_stats["total_requests"]
            )

            self._gauges["api_success_rate"] = (
                self._api_stats["successful_requests"]
                / self._api_stats["total_requests"]
            ) * 100

        except Exception as e:
            logger.error("Failed to record API metrics", error=str(e))

    def get_execution_statistics(self) -> Dict[str, Any]:
        """Get execution statistics summary."""
        stats = dict(self._execution_stats)

        # Convert defaultdicts to regular dicts
        stats["language_counts"] = dict(stats["language_counts"])
        stats["hourly_executions"] = dict(stats["hourly_executions"])

        # Add calculated metrics
        if stats["total_executions"] > 0:
            stats["success_rate"] = (
                stats["successful_executions"] / stats["total_executions"]
            ) * 100
            stats["failure_rate"] = (
                stats["failed_executions"] / stats["total_executions"]
            ) * 100
            stats["timeout_rate"] = (
                stats["timeout_executions"] / stats["total_executions"]
            ) * 100

        # Add histogram statistics
        if (
            "execution_time_ms" in self._histograms
            and self._histograms["execution_time_ms"]
        ):
            times = self._histograms["execution_time_ms"]
            stats["execution_time_percentiles"] = {
                "p50": self._percentile(times, 50),
                "p90": self._percentile(times, 90),
                "p95": self._percentile(times, 95),
                "p99": self._percentile(times, 99),
            }

        if (
            "memory_usage_mb" in self._histograms
            and self._histograms["memory_usage_mb"]
        ):
            memory = self._histograms["memory_usage_mb"]
            stats["memory_usage_percentiles"] = {
                "p50": self._percentile(memory, 50),
                "p90": self._percentile(memory, 90),
                "p95": self._percentile(memory, 95),
                "p99": self._percentile(memory, 99),
            }

        return stats

    def get_api_statistics(self) -> Dict[str, Any]:
        """Get API statistics summary."""
        stats = dict(self._api_stats)

        # Convert defaultdicts to regular dicts
        stats["endpoint_counts"] = dict(stats["endpoint_counts"])
        stats["status_code_counts"] = dict(stats["status_code_counts"])
        stats["hourly_requests"] = dict(stats["hourly_requests"])

        # Add histogram statistics
        if (
            "api_response_time_ms" in self._histograms
            and self._histograms["api_response_time_ms"]
        ):
            times = self._histograms["api_response_time_ms"]
            stats["response_time_percentiles"] = {
                "p50": self._percentile(times, 50),
                "p90": self._percentile(times, 90),
                "p95": self._percentile(times, 95),
                "p99": self._percentile(times, 99),
            }

        return stats

    def get_system_metrics(self) -> Dict[str, Any]:
        """Get current system metrics."""
        return {
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "buffer_size": len(self._metrics_buffer),
            "uptime_seconds": time.time() - getattr(self, "_start_time", time.time()),
            "last_persistence": getattr(self, "_last_persistence", None),
        }

    def _percentile(self, data: List[float], percentile: float) -> float:
        """Calculate percentile of a list of values."""
        if not data:
            return 0.0

        sorted_data = sorted(data)
        index = (percentile / 100) * (len(sorted_data) - 1)

        if index.is_integer():
            return sorted_data[int(index)]
        else:
            lower = sorted_data[int(index)]
            upper = sorted_data[int(index) + 1]
            return lower + (upper - lower) * (index - int(index))

    async def _persistence_loop(self) -> None:
        """Background task for persisting metrics to Redis."""
        while True:
            try:
                await asyncio.sleep(self._persistence_interval)
                await self._persist_metrics_to_redis()

            except asyncio.CancelledError:
                logger.info("Metrics persistence task cancelled")
                break
            except Exception as e:
                logger.error("Error in metrics persistence loop", error=str(e))
                # Continue the loop even if persistence fails

    async def _persist_metrics_to_redis(self) -> None:
        """Persist current metrics to Redis."""
        if not self._redis_client:
            return

        try:
            # Prepare metrics data
            metrics_data = {
                "execution_stats": self.get_execution_statistics(),
                "api_stats": self.get_api_statistics(),
                "system_metrics": self.get_system_metrics(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            # Store in Redis with TTL
            await self._redis_client.setex(
                "metrics:current", 86400, str(metrics_data)  # 24 hours TTL
            )

            # Store historical data (keep last 24 hours)
            hour_key = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")
            await self._redis_client.setex(
                f"metrics:hourly:{hour_key}",
                86400 * 7,  # 7 days TTL for hourly data
                str(metrics_data),
            )

            self._last_persistence = datetime.now(timezone.utc)

        except Exception as e:
            logger.error("Failed to persist metrics to Redis", error=str(e))

    async def _load_metrics_from_redis(self) -> None:
        """Load existing metrics from Redis."""
        if not self._redis_client:
            return

        try:
            # Load current metrics
            current_data = await self._redis_client.get("metrics:current")
            if current_data:
                # In a full implementation, we would parse and restore the metrics
                # For now, just log that we found existing data
                logger.info("Found existing metrics data in Redis")

        except Exception as e:
            logger.error("Failed to load metrics from Redis", error=str(e))


# Global metrics collector instance
metrics_collector = MetricsCollector()
