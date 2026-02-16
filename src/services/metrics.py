"""Unified metrics service combining in-memory counters, SQLite persistence,
and container pool event tracking.

Replaces the previous three-service architecture:
- MetricsCollector (in-memory + Redis persistence)
- DetailedMetricsService (Redis per-key/per-language)
- SQLiteMetricsService (SQLite long-term storage)

Redis is no longer used for metrics storage.
"""

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite
import structlog

from ..config import settings
from ..models.metrics import DetailedExecutionMetrics

logger = structlog.get_logger(__name__)

# SQLite schema -- identical to the previous sqlite_metrics.py
SCHEMA_SQL = """
-- Individual execution records (90-day retention by default)
CREATE TABLE IF NOT EXISTS executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    api_key_hash TEXT NOT NULL,
    user_id TEXT,
    entity_id TEXT,
    language TEXT NOT NULL,
    status TEXT NOT NULL,
    execution_time_ms REAL NOT NULL,
    memory_peak_mb REAL,
    cpu_time_ms REAL,
    container_source TEXT,
    repl_mode INTEGER DEFAULT 0,
    files_uploaded INTEGER DEFAULT 0,
    files_generated INTEGER DEFAULT 0,
    output_size_bytes INTEGER DEFAULT 0,
    state_size_bytes INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Daily aggregates (1-year retention by default)
CREATE TABLE IF NOT EXISTS daily_aggregates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE NOT NULL,
    api_key_hash TEXT,
    language TEXT,
    execution_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    timeout_count INTEGER DEFAULT 0,
    total_execution_time_ms REAL DEFAULT 0,
    total_memory_mb REAL DEFAULT 0,
    pool_hits INTEGER DEFAULT 0,
    pool_misses INTEGER DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, api_key_hash, language)
);

-- Hourly activity for heatmap (90-day retention)
CREATE TABLE IF NOT EXISTS hourly_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE NOT NULL,
    hour INTEGER NOT NULL,
    day_of_week INTEGER NOT NULL,
    api_key_hash TEXT,
    execution_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    avg_execution_time_ms REAL,
    UNIQUE(date, hour, api_key_hash)
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_executions_created_at ON executions(created_at);
CREATE INDEX IF NOT EXISTS idx_executions_api_key_hash ON executions(api_key_hash);
CREATE INDEX IF NOT EXISTS idx_executions_language ON executions(language);
CREATE INDEX IF NOT EXISTS idx_executions_status ON executions(status);
CREATE INDEX IF NOT EXISTS idx_executions_composite ON executions(created_at, api_key_hash, language);

CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_aggregates(date);
CREATE INDEX IF NOT EXISTS idx_daily_api_key ON daily_aggregates(api_key_hash);
CREATE INDEX IF NOT EXISTS idx_daily_language ON daily_aggregates(language);

CREATE INDEX IF NOT EXISTS idx_hourly_date ON hourly_activity(date);
CREATE INDEX IF NOT EXISTS idx_hourly_dow_hour ON hourly_activity(day_of_week, hour);
"""


@dataclass
class APIRequestMetrics:
    """Lightweight API request metrics for in-memory tracking."""

    endpoint: str
    method: str
    status_code: int
    response_time_ms: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class MetricsService:
    """Unified metrics service.

    Combines:
    - In-memory counters for fast health-check responses
    - SQLite persistence for dashboard queries and long-term analytics
    - Event hooks for container pool metrics
    """

    def __init__(self):
        # In-memory counters (for /metrics and /health endpoints)
        self._start_time = time.time()
        self._counters: Dict[str, float] = defaultdict(float)
        self._execution_times: List[float] = []
        self._api_response_times: List[float] = []

        self._execution_stats = {
            "total_executions": 0,
            "successful_executions": 0,
            "failed_executions": 0,
            "timeout_executions": 0,
            "total_execution_time_ms": 0.0,
            "language_counts": defaultdict(int),
        }

        self._api_stats = {
            "total_requests": 0,
            "successful_requests": 0,
            "error_requests": 0,
            "total_response_time_ms": 0.0,
            "endpoint_counts": defaultdict(int),
            "status_code_counts": defaultdict(int),
        }

        # Pool stats (in-memory, updated via event handlers)
        self._pool_stats = {
            "total_acquisitions": 0,
            "pool_hits": 0,
            "pool_misses": 0,
            "exhaustion_events": 0,
            "total_acquire_time_ms": 0.0,
        }

        # SQLite state
        self._db: Optional[aiosqlite.Connection] = None
        self._write_queue: asyncio.Queue = asyncio.Queue()
        self._writer_task: Optional[asyncio.Task] = None
        self._aggregation_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False
        self._batch_size = 100
        self._flush_interval = 5.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the metrics service (SQLite + background tasks)."""
        if self._running:
            return

        self._start_time = time.time()

        if not settings.sqlite_metrics_enabled:
            self._running = True
            logger.info("Metrics service started (in-memory only, SQLite disabled)")
            return

        try:
            db_dir = Path(settings.sqlite_metrics_db_path).parent
            db_dir.mkdir(parents=True, exist_ok=True)

            self._db = await aiosqlite.connect(settings.sqlite_metrics_db_path)
            self._db.row_factory = aiosqlite.Row

            await self._db.execute("PRAGMA journal_mode=WAL")
            await self._db.execute("PRAGMA synchronous=NORMAL")
            await self._db.execute("PRAGMA cache_size=10000")
            await self._db.executescript(SCHEMA_SQL)
            await self._db.commit()

            self._running = True

            self._writer_task = asyncio.create_task(self._batch_writer())
            self._aggregation_task = asyncio.create_task(self._aggregation_loop())
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

            logger.info(
                "Metrics service started",
                db_path=settings.sqlite_metrics_db_path,
            )
        except Exception as e:
            self._running = True  # still run in-memory mode
            logger.warning(
                "SQLite init failed, metrics service running in-memory only",
                error=str(e),
            )

    async def stop(self) -> None:
        """Stop the metrics service and flush pending writes."""
        if not self._running:
            return

        self._running = False

        for task in [self._writer_task, self._aggregation_task, self._cleanup_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        await self._flush_queue()

        if self._db:
            await self._db.close()
            self._db = None

        logger.info("Metrics service stopped")

    def register_event_handlers(self) -> None:
        """Register event handlers for container pool metrics."""
        try:
            from ..core.events import (
                event_bus,
                ContainerAcquiredFromPool,
                ContainerCreatedFresh,
                PoolExhausted,
            )

            @event_bus.subscribe(ContainerAcquiredFromPool)
            async def handle_pool_hit(event: ContainerAcquiredFromPool):
                self._pool_stats["pool_hits"] += 1
                self._pool_stats["total_acquisitions"] += 1
                self._pool_stats["total_acquire_time_ms"] += event.acquire_time_ms

            @event_bus.subscribe(ContainerCreatedFresh)
            async def handle_pool_miss(event: ContainerCreatedFresh):
                if event.reason in ("pool_empty", "pool_disabled"):
                    self._pool_stats["pool_misses"] += 1
                    self._pool_stats["total_acquisitions"] += 1

            @event_bus.subscribe(PoolExhausted)
            async def handle_pool_exhaustion(event: PoolExhausted):
                self._pool_stats["exhaustion_events"] += 1

            logger.info("Registered pool event handlers for metrics")
        except Exception as e:
            logger.warning("Failed to register pool event handlers", error=str(e))

    # ------------------------------------------------------------------
    # Recording methods
    # ------------------------------------------------------------------

    async def record_execution(self, metrics: DetailedExecutionMetrics) -> None:
        """Record an execution -- updates in-memory counters and queues SQLite write."""
        # Update in-memory counters
        self._counters["executions_total"] += 1
        self._counters[f"executions_by_language.{metrics.language}"] += 1
        self._counters[f"executions_by_status.{metrics.status}"] += 1

        stats = self._execution_stats
        stats["total_executions"] += 1
        stats["total_execution_time_ms"] += metrics.execution_time_ms
        stats["language_counts"][metrics.language] += 1

        if metrics.status == "completed":
            stats["successful_executions"] += 1
        elif metrics.status == "failed":
            stats["failed_executions"] += 1
        elif metrics.status == "timeout":
            stats["timeout_executions"] += 1

        # Track execution times for percentiles (keep bounded)
        self._execution_times.append(metrics.execution_time_ms)
        if len(self._execution_times) > 1000:
            self._execution_times = self._execution_times[-500:]

        # Queue for SQLite persistence
        if self._running and self._db is not None:
            await self._write_queue.put(metrics)

    def record_api_request(self, metrics: APIRequestMetrics) -> None:
        """Record an API request (in-memory only, no persistence needed)."""
        self._counters["api_requests_total"] += 1
        self._counters[f"api_requests_by_endpoint.{metrics.endpoint}"] += 1
        self._counters[f"api_requests_by_status.{metrics.status_code}"] += 1

        api = self._api_stats
        api["total_requests"] += 1
        api["total_response_time_ms"] += metrics.response_time_ms
        api["endpoint_counts"][metrics.endpoint] += 1
        api["status_code_counts"][metrics.status_code] += 1

        if 200 <= metrics.status_code < 400:
            api["successful_requests"] += 1
        else:
            api["error_requests"] += 1

        self._api_response_times.append(metrics.response_time_ms)
        if len(self._api_response_times) > 1000:
            self._api_response_times = self._api_response_times[-500:]

    # ------------------------------------------------------------------
    # In-memory query methods (used by /metrics and /health endpoints)
    # ------------------------------------------------------------------

    def get_execution_statistics(self) -> Dict[str, Any]:
        """Get execution statistics summary (in-memory)."""
        stats = {
            k: (dict(v) if isinstance(v, defaultdict) else v)
            for k, v in self._execution_stats.items()
        }

        total = stats["total_executions"]
        if total > 0:
            stats["success_rate"] = (stats["successful_executions"] / total) * 100
            stats["failure_rate"] = (stats["failed_executions"] / total) * 100
            stats["timeout_rate"] = (stats["timeout_executions"] / total) * 100

        if self._execution_times:
            stats["execution_time_percentiles"] = {
                "p50": self._percentile(self._execution_times, 50),
                "p90": self._percentile(self._execution_times, 90),
                "p95": self._percentile(self._execution_times, 95),
                "p99": self._percentile(self._execution_times, 99),
            }

        return stats

    def get_api_statistics(self) -> Dict[str, Any]:
        """Get API statistics summary (in-memory)."""
        stats = {
            k: (dict(v) if isinstance(v, defaultdict) else v)
            for k, v in self._api_stats.items()
        }

        if self._api_response_times:
            stats["response_time_percentiles"] = {
                "p50": self._percentile(self._api_response_times, 50),
                "p90": self._percentile(self._api_response_times, 90),
                "p95": self._percentile(self._api_response_times, 95),
                "p99": self._percentile(self._api_response_times, 99),
            }

        return stats

    def get_system_metrics(self) -> Dict[str, Any]:
        """Get current system metrics (in-memory)."""
        return {
            "counters": dict(self._counters),
            "gauges": {},
            "buffer_size": self._write_queue.qsize() if self._db else 0,
            "uptime_seconds": time.time() - self._start_time,
        }

    def get_pool_stats(self) -> Dict[str, Any]:
        """Get container pool statistics (in-memory)."""
        total = self._pool_stats["total_acquisitions"]
        hit_rate = (self._pool_stats["pool_hits"] / total * 100) if total > 0 else 0.0
        avg_acquire = (
            (self._pool_stats["total_acquire_time_ms"] / total) if total > 0 else 0.0
        )
        return {
            "total_acquisitions": total,
            "pool_hits": self._pool_stats["pool_hits"],
            "pool_misses": self._pool_stats["pool_misses"],
            "hit_rate": round(hit_rate, 1),
            "avg_acquire_time_ms": round(avg_acquire, 1),
            "exhaustion_events": self._pool_stats["exhaustion_events"],
        }

    # ------------------------------------------------------------------
    # SQLite query methods (used by dashboard_metrics.py endpoints)
    # ------------------------------------------------------------------

    async def get_summary_stats(
        self,
        start: datetime,
        end: datetime,
        api_key_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get summary statistics for stats cards."""
        if not self._db:
            return {}

        params: List[Any] = [start.isoformat(), end.isoformat()]
        api_key_filter = ""
        if api_key_hash:
            api_key_filter = "AND api_key_hash = ?"
            params.append(api_key_hash)

        cursor = await self._db.execute(
            f"""
            SELECT
                COUNT(*) as total_executions,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as success_count,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failure_count,
                SUM(CASE WHEN status = 'timeout' THEN 1 ELSE 0 END) as timeout_count,
                AVG(execution_time_ms) as avg_execution_time_ms,
                SUM(CASE WHEN container_source = 'pool_hit' THEN 1 ELSE 0 END) as pool_hits,
                SUM(CASE WHEN container_source IN ('pool_hit', 'pool_miss') THEN 1 ELSE 0 END) as pool_total,
                COUNT(DISTINCT api_key_hash) as active_api_keys
            FROM executions
            WHERE created_at >= ? AND created_at <= ? {api_key_filter}
            """,
            params,
        )
        row = await cursor.fetchone()

        if not row or row["total_executions"] == 0:
            return {
                "total_executions": 0,
                "success_rate": 0,
                "avg_execution_time_ms": 0,
                "pool_hit_rate": 0,
                "active_api_keys": 0,
            }

        total = row["total_executions"]
        success_rate = (row["success_count"] / total * 100) if total > 0 else 0
        pool_hit_rate = (
            (row["pool_hits"] / row["pool_total"] * 100) if row["pool_total"] > 0 else 0
        )

        return {
            "total_executions": total,
            "success_count": row["success_count"] or 0,
            "failure_count": row["failure_count"] or 0,
            "timeout_count": row["timeout_count"] or 0,
            "success_rate": round(success_rate, 1),
            "avg_execution_time_ms": round(row["avg_execution_time_ms"] or 0, 1),
            "pool_hit_rate": round(pool_hit_rate, 1),
            "active_api_keys": row["active_api_keys"] or 0,
        }

    async def get_language_usage(
        self,
        start: datetime,
        end: datetime,
        api_key_hash: Optional[str] = None,
        stack_by_api_key: bool = False,
    ) -> Dict[str, Any]:
        """Get language usage data for stacked bar chart."""
        if not self._db:
            return {"by_language": {}, "by_api_key": {}, "matrix": {}}

        params: List[Any] = [start.isoformat(), end.isoformat()]
        api_key_filter = ""
        if api_key_hash:
            api_key_filter = "AND api_key_hash = ?"
            params.append(api_key_hash)

        cursor = await self._db.execute(
            f"""
            SELECT language, COUNT(*) as count
            FROM executions
            WHERE created_at >= ? AND created_at <= ? {api_key_filter}
            GROUP BY language
            ORDER BY count DESC
            """,
            params,
        )
        by_language = {row["language"]: row["count"] async for row in cursor}

        if not stack_by_api_key:
            return {"by_language": by_language, "by_api_key": {}, "matrix": {}}

        params = [start.isoformat(), end.isoformat()]
        cursor = await self._db.execute(
            """
            SELECT language, api_key_hash, COUNT(*) as count
            FROM executions
            WHERE created_at >= ? AND created_at <= ?
            GROUP BY language, api_key_hash
            ORDER BY language, count DESC
            """,
            params,
        )

        matrix: Dict[str, Dict[str, int]] = {}
        api_keys_seen: Dict[str, int] = {}

        async for row in cursor:
            lang = row["language"]
            key = row["api_key_hash"]
            count = row["count"]

            if lang not in matrix:
                matrix[lang] = {}
            matrix[lang][key] = count

            if key not in api_keys_seen:
                api_keys_seen[key] = 0
            api_keys_seen[key] += count

        return {
            "by_language": by_language,
            "by_api_key": api_keys_seen,
            "matrix": matrix,
        }

    async def get_time_series(
        self,
        start: datetime,
        end: datetime,
        api_key_hash: Optional[str] = None,
        granularity: str = "hour",
    ) -> Dict[str, Any]:
        """Get execution trend data for line chart."""
        if not self._db:
            return {
                "timestamps": [],
                "executions": [],
                "success_rate": [],
                "avg_duration": [],
            }

        params: List[Any] = [start.isoformat(), end.isoformat()]
        api_key_filter = ""
        if api_key_hash:
            api_key_filter = "AND api_key_hash = ?"
            params.append(api_key_hash)

        if granularity == "hour":
            time_format = "%Y-%m-%d %H:00"
        elif granularity == "day":
            time_format = "%Y-%m-%d"
        else:
            time_format = "%Y-%W"

        cursor = await self._db.execute(
            f"""
            SELECT
                strftime('{time_format}', created_at) as period,
                COUNT(*) as executions,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as success_count,
                AVG(execution_time_ms) as avg_duration
            FROM executions
            WHERE created_at >= ? AND created_at <= ? {api_key_filter}
            GROUP BY period
            ORDER BY period
            """,
            params,
        )

        timestamps = []
        executions = []
        success_rate = []
        avg_duration = []

        async for row in cursor:
            timestamps.append(row["period"])
            executions.append(row["executions"])
            rate = (
                (row["success_count"] / row["executions"] * 100)
                if row["executions"] > 0
                else 0
            )
            success_rate.append(round(rate, 1))
            avg_duration.append(round(row["avg_duration"] or 0, 1))

        return {
            "timestamps": timestamps,
            "executions": executions,
            "success_rate": success_rate,
            "avg_duration": avg_duration,
        }

    async def get_heatmap_data(
        self,
        start: datetime,
        end: datetime,
        api_key_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get day-of-week x hour activity matrix for heatmap."""
        if not self._db:
            return {"matrix": [[0] * 24 for _ in range(7)], "max_value": 0}

        params: List[Any] = [start.isoformat(), end.isoformat()]
        api_key_filter = ""
        if api_key_hash:
            api_key_filter = "AND api_key_hash = ?"
            params.append(api_key_hash)

        cursor = await self._db.execute(
            f"""
            SELECT
                CAST(strftime('%w', created_at) AS INTEGER) as day_of_week,
                CAST(strftime('%H', created_at) AS INTEGER) as hour,
                COUNT(*) as count
            FROM executions
            WHERE created_at >= ? AND created_at <= ? {api_key_filter}
            GROUP BY day_of_week, hour
            """,
            params,
        )

        matrix = [[0] * 24 for _ in range(7)]
        max_value = 0

        async for row in cursor:
            dow = (row["day_of_week"] - 1) % 7
            hour = row["hour"]
            count = row["count"]
            matrix[dow][hour] = count
            max_value = max(max_value, count)

        return {"matrix": matrix, "max_value": max_value}

    async def get_api_keys_list(self) -> List[Dict[str, Any]]:
        """Get list of API keys for filter dropdown."""
        if not self._db:
            return []

        cursor = await self._db.execute("""
            SELECT DISTINCT api_key_hash, COUNT(*) as usage_count
            FROM executions
            GROUP BY api_key_hash
            ORDER BY usage_count DESC
            LIMIT 50
            """)

        return [
            {"key_hash": row["api_key_hash"], "usage_count": row["usage_count"]}
            async for row in cursor
        ]

    async def get_top_languages(
        self,
        start: datetime,
        end: datetime,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Get top languages by execution count."""
        if not self._db:
            return []

        cursor = await self._db.execute(
            """
            SELECT language, COUNT(*) as count
            FROM executions
            WHERE created_at >= ? AND created_at <= ?
            GROUP BY language
            ORDER BY count DESC
            LIMIT ?
            """,
            (start.isoformat(), end.isoformat(), limit),
        )

        return [
            {"language": row["language"], "count": row["count"]} async for row in cursor
        ]

    # ------------------------------------------------------------------
    # SQLite background tasks
    # ------------------------------------------------------------------

    async def _batch_writer(self) -> None:
        """Background task that batches writes for efficiency."""
        batch: List[DetailedExecutionMetrics] = []

        while self._running:
            try:
                try:
                    item = await asyncio.wait_for(
                        self._write_queue.get(), timeout=self._flush_interval
                    )
                    batch.append(item)
                except asyncio.TimeoutError:
                    pass

                if len(batch) >= self._batch_size or (
                    batch and self._write_queue.empty()
                ):
                    await self._write_batch(batch)
                    batch = []

            except asyncio.CancelledError:
                if batch:
                    await self._write_batch(batch)
                raise
            except Exception as e:
                logger.error("Error in batch writer", error=str(e))

    async def _write_batch(self, batch: List[DetailedExecutionMetrics]) -> None:
        """Write a batch of execution records to SQLite."""
        if not batch or not self._db:
            return

        try:
            await self._db.executemany(
                """
                INSERT OR IGNORE INTO executions (
                    execution_id, session_id, api_key_hash, user_id, entity_id,
                    language, status, execution_time_ms, memory_peak_mb, cpu_time_ms,
                    container_source, repl_mode, files_uploaded, files_generated,
                    output_size_bytes, state_size_bytes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        m.execution_id,
                        m.session_id,
                        m.api_key_hash[:16] if m.api_key_hash else "unknown",
                        m.user_id,
                        m.entity_id,
                        m.language,
                        m.status,
                        m.execution_time_ms,
                        m.memory_peak_mb,
                        m.cpu_time_ms,
                        m.container_source,
                        1 if m.repl_mode else 0,
                        m.files_uploaded,
                        m.files_generated,
                        m.output_size_bytes,
                        m.state_size_bytes,
                        (
                            m.timestamp.isoformat()
                            if m.timestamp
                            else datetime.now(timezone.utc).isoformat()
                        ),
                    )
                    for m in batch
                ],
            )
            await self._db.commit()
            logger.debug("Wrote metrics batch", count=len(batch))
        except Exception as e:
            logger.error("Failed to write metrics batch", error=str(e))

    async def _flush_queue(self) -> None:
        """Flush all pending writes from the queue."""
        batch: List[DetailedExecutionMetrics] = []
        while not self._write_queue.empty():
            try:
                batch.append(self._write_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if batch:
            await self._write_batch(batch)

    async def _aggregation_loop(self) -> None:
        """Periodically aggregate executions into daily summaries."""
        interval = settings.metrics_aggregation_interval_minutes * 60

        while self._running:
            try:
                await asyncio.sleep(interval)
                await self.run_aggregation()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Error in aggregation loop", error=str(e))

    async def run_aggregation(self) -> None:
        """Build daily aggregates from execution records."""
        if not self._db:
            return

        try:
            yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()

            await self._db.execute(
                """
                INSERT OR REPLACE INTO daily_aggregates (
                    date, api_key_hash, language,
                    execution_count, success_count, failure_count, timeout_count,
                    total_execution_time_ms, total_memory_mb, pool_hits, pool_misses
                )
                SELECT
                    DATE(created_at) as date,
                    api_key_hash,
                    language,
                    COUNT(*) as execution_count,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as success_count,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failure_count,
                    SUM(CASE WHEN status = 'timeout' THEN 1 ELSE 0 END) as timeout_count,
                    SUM(execution_time_ms) as total_execution_time_ms,
                    SUM(COALESCE(memory_peak_mb, 0)) as total_memory_mb,
                    SUM(CASE WHEN container_source = 'pool_hit' THEN 1 ELSE 0 END) as pool_hits,
                    SUM(CASE WHEN container_source = 'pool_miss' THEN 1 ELSE 0 END) as pool_misses
                FROM executions
                WHERE DATE(created_at) <= ?
                GROUP BY DATE(created_at), api_key_hash, language
                """,
                (yesterday.isoformat(),),
            )

            await self._db.execute(
                """
                INSERT OR REPLACE INTO hourly_activity (
                    date, hour, day_of_week, api_key_hash,
                    execution_count, success_count, avg_execution_time_ms
                )
                SELECT
                    DATE(created_at) as date,
                    CAST(strftime('%H', created_at) AS INTEGER) as hour,
                    CAST(strftime('%w', created_at) AS INTEGER) as day_of_week,
                    api_key_hash,
                    COUNT(*) as execution_count,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as success_count,
                    AVG(execution_time_ms) as avg_execution_time_ms
                FROM executions
                WHERE DATE(created_at) <= ?
                GROUP BY DATE(created_at), hour, api_key_hash
                """,
                (yesterday.isoformat(),),
            )

            await self._db.commit()
            logger.info("Aggregation completed", up_to_date=yesterday.isoformat())
        except Exception as e:
            logger.error("Aggregation failed", error=str(e))

    async def _cleanup_loop(self) -> None:
        """Periodically clean up old data based on retention settings."""
        interval = 24 * 60 * 60

        while self._running:
            try:
                await asyncio.sleep(interval)
                await self.cleanup_old_data()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Error in cleanup loop", error=str(e))

    async def cleanup_old_data(self) -> None:
        """Remove data older than retention periods."""
        if not self._db:
            return

        try:
            now = datetime.now(timezone.utc)

            exec_cutoff = (
                now - timedelta(days=settings.metrics_execution_retention_days)
            ).isoformat()
            result = await self._db.execute(
                "DELETE FROM executions WHERE created_at < ?", (exec_cutoff,)
            )
            exec_deleted = result.rowcount

            daily_cutoff = (
                (now - timedelta(days=settings.metrics_daily_retention_days))
                .date()
                .isoformat()
            )
            result = await self._db.execute(
                "DELETE FROM daily_aggregates WHERE date < ?", (daily_cutoff,)
            )
            daily_deleted = result.rowcount

            hourly_cutoff = (
                (now - timedelta(days=settings.metrics_execution_retention_days))
                .date()
                .isoformat()
            )
            result = await self._db.execute(
                "DELETE FROM hourly_activity WHERE date < ?", (hourly_cutoff,)
            )
            hourly_deleted = result.rowcount

            await self._db.commit()
            await self._db.execute("VACUUM")

            logger.info(
                "Cleanup completed",
                executions_deleted=exec_deleted,
                daily_deleted=daily_deleted,
                hourly_deleted=hourly_deleted,
            )
        except Exception as e:
            logger.error("Cleanup failed", error=str(e))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _percentile(data: List[float], percentile: float) -> float:
        """Calculate percentile of a list of values."""
        if not data:
            return 0.0
        sorted_data = sorted(data)
        index = (percentile / 100) * (len(sorted_data) - 1)
        if index.is_integer():
            return sorted_data[int(index)]
        lower = sorted_data[int(index)]
        upper = sorted_data[int(index) + 1]
        return lower + (upper - lower) * (index - int(index))


# Global singleton
metrics_service = MetricsService()
