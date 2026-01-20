"""SQLite-based metrics storage for long-term analytics.

This module provides persistent storage for execution metrics using SQLite,
enabling historical analytics, time-series charts, and dashboard visualizations.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

from src.config import settings
from src.models.metrics import DetailedExecutionMetrics
from src.utils.logging import get_logger

logger = get_logger(__name__)

# SQL Schema
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


class SQLiteMetricsService:
    """SQLite-based metrics storage for long-term analytics."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or settings.sqlite_metrics_db_path
        self._db: Optional[aiosqlite.Connection] = None
        self._write_queue: asyncio.Queue = asyncio.Queue()
        self._writer_task: Optional[asyncio.Task] = None
        self._aggregation_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False
        self._batch_size = 100
        self._flush_interval = 5.0  # seconds

    async def start(self) -> None:
        """Initialize database and start background tasks."""
        if self._running:
            return

        # Ensure data directory exists
        db_dir = Path(self.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

        # Connect to database
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row

        # Enable WAL mode for better concurrent read/write performance
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA cache_size=10000")

        # Create schema
        await self._db.executescript(SCHEMA_SQL)
        await self._db.commit()

        self._running = True

        # Start background tasks
        self._writer_task = asyncio.create_task(self._batch_writer())
        self._aggregation_task = asyncio.create_task(self._aggregation_loop())
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

        logger.info("SQLite metrics service started", db_path=self.db_path)

    async def stop(self) -> None:
        """Flush pending writes and close connection."""
        if not self._running:
            return

        self._running = False

        # Cancel background tasks
        for task in [self._writer_task, self._aggregation_task, self._cleanup_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Flush remaining writes
        await self._flush_queue()

        # Close database
        if self._db:
            await self._db.close()
            self._db = None

        logger.info("SQLite metrics service stopped")

    async def record_execution(self, metrics: DetailedExecutionMetrics) -> None:
        """Queue an execution record for batch writing."""
        if not self._running:
            return

        await self._write_queue.put(metrics)

    async def _batch_writer(self) -> None:
        """Background task that batches writes for efficiency."""
        batch: List[DetailedExecutionMetrics] = []

        while self._running:
            try:
                # Wait for items with timeout
                try:
                    item = await asyncio.wait_for(
                        self._write_queue.get(), timeout=self._flush_interval
                    )
                    batch.append(item)
                except asyncio.TimeoutError:
                    pass

                # Flush if batch is full or timeout occurred
                if len(batch) >= self._batch_size or (
                    batch and self._write_queue.empty()
                ):
                    await self._write_batch(batch)
                    batch = []

            except asyncio.CancelledError:
                # Flush remaining on shutdown
                if batch:
                    await self._write_batch(batch)
                raise
            except Exception as e:
                logger.error("Error in batch writer", error=str(e))

    async def _write_batch(self, batch: List[DetailedExecutionMetrics]) -> None:
        """Write a batch of execution records to the database."""
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
            # Get yesterday's date for aggregation
            yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()

            # Aggregate by date, api_key, language
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

            # Aggregate hourly activity
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
        # Run cleanup once per day
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

            # Clean up old executions
            exec_cutoff = (
                now - timedelta(days=settings.metrics_execution_retention_days)
            ).isoformat()
            result = await self._db.execute(
                "DELETE FROM executions WHERE created_at < ?", (exec_cutoff,)
            )
            exec_deleted = result.rowcount

            # Clean up old daily aggregates
            daily_cutoff = (
                (now - timedelta(days=settings.metrics_daily_retention_days))
                .date()
                .isoformat()
            )
            result = await self._db.execute(
                "DELETE FROM daily_aggregates WHERE date < ?", (daily_cutoff,)
            )
            daily_deleted = result.rowcount

            # Clean up old hourly activity
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

            # Vacuum to reclaim space
            await self._db.execute("VACUUM")

            logger.info(
                "Cleanup completed",
                executions_deleted=exec_deleted,
                daily_deleted=daily_deleted,
                hourly_deleted=hourly_deleted,
            )
        except Exception as e:
            logger.error("Cleanup failed", error=str(e))

    # =========================================================================
    # Query Methods for Dashboard
    # =========================================================================

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

        # Get totals by language
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

        # Get stacked data: language x api_key matrix
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

        # Determine time grouping format
        if granularity == "hour":
            time_format = "%Y-%m-%d %H:00"
        elif granularity == "day":
            time_format = "%Y-%m-%d"
        else:  # week
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

        # Initialize 7x24 matrix (0=Sunday in SQLite, we'll adjust to 0=Monday)
        matrix = [[0] * 24 for _ in range(7)]
        max_value = 0

        async for row in cursor:
            # SQLite: 0=Sunday, 1=Monday, ..., 6=Saturday
            # Convert to: 0=Monday, 1=Tuesday, ..., 6=Sunday
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


# Global service instance
sqlite_metrics_service = SQLiteMetricsService()
