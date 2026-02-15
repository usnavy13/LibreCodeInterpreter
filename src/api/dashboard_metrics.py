"""Dashboard metrics API endpoints for advanced analytics."""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..dependencies.auth import verify_master_key
from ..services.metrics import metrics_service
from ..services.api_key_manager import get_api_key_manager

router = APIRouter(prefix="/admin/metrics", tags=["admin-metrics"])


def get_date_range(
    period: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> tuple[datetime, datetime]:
    """Calculate date range from period or custom dates."""
    now = datetime.now(timezone.utc)
    end = end_date or now

    if start_date:
        return start_date, end

    # Calculate start based on period
    period_mapping = {
        "hour": timedelta(hours=1),
        "day": timedelta(days=1),
        "week": timedelta(weeks=1),
        "month": timedelta(days=30),
    }
    delta = period_mapping.get(period, timedelta(days=1))
    start = end - delta

    return start, end


def get_granularity(period: str) -> str:
    """Determine appropriate granularity for time-series based on period."""
    if period == "hour":
        return "hour"  # Will show minute-level but group by hour
    elif period == "day":
        return "hour"
    elif period == "week":
        return "day"
    else:  # month
        return "day"


# --- Response Models ---


class SummaryResponse(BaseModel):
    total_executions: int
    success_count: int
    failure_count: int
    timeout_count: int
    success_rate: float
    avg_execution_time_ms: float
    pool_hit_rate: float
    active_api_keys: int
    period: str
    start_date: str
    end_date: str


class LanguageUsageResponse(BaseModel):
    by_language: Dict[str, int]
    by_api_key: Dict[str, int]
    matrix: Dict[str, Dict[str, int]]


class TimeSeriesResponse(BaseModel):
    timestamps: List[str]
    executions: List[int]
    success_rate: List[float]
    avg_duration: List[float]
    granularity: str


class HeatmapResponse(BaseModel):
    matrix: List[List[int]]
    max_value: int
    days: List[str]
    hours: List[int]


class ApiKeyFilterOption(BaseModel):
    key_hash: str
    name: str
    key_prefix: str
    usage_count: int
    source: str = "managed"  # "managed" or "environment"


# --- Endpoints ---


@router.get("/summary", response_model=SummaryResponse)
async def get_metrics_summary(
    period: Literal["hour", "day", "week", "month"] = "day",
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    api_key_hash: Optional[str] = None,
    _: str = Depends(verify_master_key),
):
    """Get summary statistics for the selected period."""
    start, end = get_date_range(period, start_date, end_date)

    stats = await metrics_service.get_summary_stats(
        start=start, end=end, api_key_hash=api_key_hash
    )

    return SummaryResponse(
        total_executions=stats.get("total_executions", 0),
        success_count=stats.get("success_count", 0),
        failure_count=stats.get("failure_count", 0),
        timeout_count=stats.get("timeout_count", 0),
        success_rate=stats.get("success_rate", 0),
        avg_execution_time_ms=stats.get("avg_execution_time_ms", 0),
        pool_hit_rate=stats.get("pool_hit_rate", 0),
        active_api_keys=stats.get("active_api_keys", 0),
        period=period,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
    )


@router.get("/languages", response_model=LanguageUsageResponse)
async def get_language_metrics(
    period: Literal["hour", "day", "week", "month"] = "day",
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    api_key_hash: Optional[str] = None,
    stack_by_api_key: bool = False,
    _: str = Depends(verify_master_key),
):
    """Get language usage data for stacked bar chart."""
    start, end = get_date_range(period, start_date, end_date)

    data = await metrics_service.get_language_usage(
        start=start,
        end=end,
        api_key_hash=api_key_hash,
        stack_by_api_key=stack_by_api_key,
    )

    return LanguageUsageResponse(
        by_language=data.get("by_language", {}),
        by_api_key=data.get("by_api_key", {}),
        matrix=data.get("matrix", {}),
    )


@router.get("/time-series", response_model=TimeSeriesResponse)
async def get_time_series(
    period: Literal["hour", "day", "week", "month"] = "day",
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    api_key_hash: Optional[str] = None,
    _: str = Depends(verify_master_key),
):
    """Get time-series data for line charts."""
    start, end = get_date_range(period, start_date, end_date)
    granularity = get_granularity(period)

    data = await metrics_service.get_time_series(
        start=start,
        end=end,
        api_key_hash=api_key_hash,
        granularity=granularity,
    )

    return TimeSeriesResponse(
        timestamps=data.get("timestamps", []),
        executions=data.get("executions", []),
        success_rate=data.get("success_rate", []),
        avg_duration=data.get("avg_duration", []),
        granularity=granularity,
    )


@router.get("/heatmap", response_model=HeatmapResponse)
async def get_activity_heatmap(
    period: Literal["hour", "day", "week", "month"] = "week",
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    api_key_hash: Optional[str] = None,
    _: str = Depends(verify_master_key),
):
    """Get hourly activity heatmap data.

    For hour/day periods, we expand to week to have meaningful heatmap data.
    """
    # Heatmap needs at least a week of data to be meaningful
    effective_period = period if period in ("week", "month") else "week"
    start, end = get_date_range(effective_period, start_date, end_date)

    data = await metrics_service.get_heatmap_data(
        start=start, end=end, api_key_hash=api_key_hash
    )

    return HeatmapResponse(
        matrix=data.get("matrix", [[0] * 24 for _ in range(7)]),
        max_value=data.get("max_value", 0),
        days=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        hours=list(range(24)),
    )


@router.get("/api-keys", response_model=List[ApiKeyFilterOption])
async def get_api_keys_for_filter(_: str = Depends(verify_master_key)):
    """Get list of API keys for filter dropdown (includes env keys)."""
    # Get API keys from manager (with names), including env keys
    manager = await get_api_key_manager()
    managed_keys = await manager.list_keys(include_env_keys=True)

    # Build lookup by key_hash
    key_lookup = {k.key_hash: k for k in managed_keys}

    # Get keys from SQLite with usage counts
    sqlite_keys = await metrics_service.get_api_keys_list()

    result = []
    seen_hashes = set()

    for sk in sqlite_keys:
        key_hash = sk["key_hash"]
        managed = key_lookup.get(key_hash)
        seen_hashes.add(key_hash)

        result.append(
            ApiKeyFilterOption(
                key_hash=key_hash,
                name=managed.name if managed else f"Key {key_hash[:8]}",
                key_prefix=managed.key_prefix if managed else key_hash[:12],
                usage_count=sk["usage_count"],
                source=managed.source if managed else "managed",
            )
        )

    # Add env keys that might not have SQLite usage yet
    for key in managed_keys:
        if key.source == "environment" and key.key_hash not in seen_hashes:
            result.append(
                ApiKeyFilterOption(
                    key_hash=key.key_hash,
                    name=key.name,
                    key_prefix=key.key_prefix,
                    usage_count=key.usage_count,
                    source="environment",
                )
            )

    return result


@router.get("/top-languages")
async def get_top_languages(
    period: Literal["hour", "day", "week", "month"] = "day",
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = Query(5, ge=1, le=20),
    _: str = Depends(verify_master_key),
):
    """Get top languages by execution count."""
    start, end = get_date_range(period, start_date, end_date)

    languages = await metrics_service.get_top_languages(
        start=start, end=end, limit=limit
    )

    return {"languages": languages, "period": period}
