"""Admin API endpoints for dashboard."""

from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field

from ..config import settings
from ..dependencies.auth import verify_master_key
from ..services.api_key_manager import get_api_key_manager
from ..services.metrics import metrics_service as unified_metrics
from ..services.health import health_service
from ..models.api_key import RateLimits as RateLimitsModel

router = APIRouter(prefix="/admin", tags=["admin"])


# --- Models ---


class RateLimitsUpdate(BaseModel):
    per_second: Optional[int] = None
    per_minute: Optional[int] = None
    hourly: Optional[int] = None
    daily: Optional[int] = None
    monthly: Optional[int] = None


class ApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1)
    rate_limits: Optional[RateLimitsUpdate] = None
    metadata: Optional[Dict[str, str]] = None


class ApiKeyUpdate(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    rate_limits: Optional[RateLimitsUpdate] = None


class ApiKeyResponse(BaseModel):
    key_hash: str
    key_prefix: str
    name: str
    created_at: datetime
    enabled: bool
    rate_limits: Dict[str, Optional[int]]
    metadata: Dict[str, str]
    last_used_at: Optional[datetime] = None
    usage_count: int
    source: str = "managed"  # "managed" or "environment"


# --- Endpoints ---


@router.get("/keys", response_model=List[ApiKeyResponse])
async def list_keys(_: str = Depends(verify_master_key)):
    """List all API keys including environment keys (read-only)."""
    manager = await get_api_key_manager()
    records = await manager.list_keys(include_env_keys=True)

    return [
        ApiKeyResponse(
            key_hash=r.key_hash,
            key_prefix=r.key_prefix,
            name=r.name,
            created_at=r.created_at,
            enabled=r.enabled,
            rate_limits=r.rate_limits.to_dict(),
            metadata=r.metadata,
            last_used_at=r.last_used_at,
            usage_count=r.usage_count,
            source=r.source,
        )
        for r in records
    ]


@router.post("/keys", response_model=Dict[str, Any])
async def create_key(data: ApiKeyCreate, _: str = Depends(verify_master_key)):
    """Create a new API key."""
    manager = await get_api_key_manager()

    rate_limits = None
    if data.rate_limits:
        rate_limits = RateLimitsModel(
            per_second=data.rate_limits.per_second,
            per_minute=data.rate_limits.per_minute,
            hourly=data.rate_limits.hourly,
            daily=data.rate_limits.daily,
            monthly=data.rate_limits.monthly,
        )

    full_key, record = await manager.create_key(
        name=data.name, rate_limits=rate_limits, metadata=data.metadata
    )

    return {
        "api_key": full_key,
        "record": ApiKeyResponse(
            key_hash=record.key_hash,
            key_prefix=record.key_prefix,
            name=record.name,
            created_at=record.created_at,
            enabled=record.enabled,
            rate_limits=record.rate_limits.to_dict(),
            metadata=record.metadata,
            last_used_at=record.last_used_at,
            usage_count=record.usage_count,
            source=record.source,
        ),
    }


@router.patch("/keys/{key_hash}", response_model=bool)
async def update_key(
    key_hash: str, data: ApiKeyUpdate, _: str = Depends(verify_master_key)
):
    """Update an API key."""
    manager = await get_api_key_manager()

    # Check if this is an env key (not allowed to modify)
    record = await manager.get_key(key_hash)
    if record and record.source == "environment":
        raise HTTPException(
            status_code=403,
            detail="Environment keys cannot be modified. Update the API_KEY environment variable instead.",
        )

    rate_limits = None
    if data.rate_limits:
        rate_limits = RateLimitsModel(
            per_second=data.rate_limits.per_second,
            per_minute=data.rate_limits.per_minute,
            hourly=data.rate_limits.hourly,
            daily=data.rate_limits.daily,
            monthly=data.rate_limits.monthly,
        )

    success = await manager.update_key(
        key_hash=key_hash, enabled=data.enabled, rate_limits=rate_limits, name=data.name
    )

    if not success:
        raise HTTPException(status_code=404, detail="Key not found")

    return success


@router.delete("/keys/{key_hash}", response_model=bool)
async def revoke_key(key_hash: str, _: str = Depends(verify_master_key)):
    """Revoke an API key."""
    manager = await get_api_key_manager()

    # Check if this is an env key (not allowed to revoke)
    record = await manager.get_key(key_hash)
    if record and record.source == "environment":
        raise HTTPException(
            status_code=403,
            detail="Environment keys cannot be revoked. Remove the API_KEY environment variable instead.",
        )

    success = await manager.revoke_key(key_hash)

    if not success:
        raise HTTPException(status_code=404, detail="Key not found")

    return success


@router.get("/stats", summary="Admin dashboard statistics")
async def get_admin_stats(
    hours: int = Query(24, ge=1, le=168), _: str = Depends(verify_master_key)
):
    """Get aggregated statistics for the admin dashboard."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=hours)

    # Get high-level summary from unified metrics service
    summary = await unified_metrics.get_summary_stats(start=start, end=now)

    # Get language breakdown
    lang_data = await unified_metrics.get_language_usage(start=start, end=now)

    # Get pool stats (in-memory)
    pool_stats = unified_metrics.get_pool_stats()

    # Get health status
    health_results = await health_service.check_all_services(use_cache=True)
    overall_health = health_service.get_overall_status(health_results)

    return {
        "summary": summary,
        "by_language": lang_data.get("by_language", {}),
        "pool_stats": pool_stats,
        "health": {
            "status": overall_health.value,
            "services": {
                name: result.to_dict() for name, result in health_results.items()
            },
        },
        "period_hours": hours,
        "timestamp": now.isoformat(),
    }
