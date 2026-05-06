"""Admin API endpoints for dashboard."""

import os
import shutil
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field

from ..config import settings
from ..dependencies.auth import verify_master_key
from ..services.api_key_manager import get_api_key_manager
from ..services.metrics import metrics_service as unified_metrics
from ..services.health import health_service
from ..models.api_key import RateLimits as RateLimitsModel

logger = structlog.get_logger(__name__)

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


@router.get("/skill-deps", summary="Inspect persistent skill-deps cache")
async def get_skill_deps_status(_: str = Depends(verify_master_key)):
    """Report on the persistent /opt/skill-deps cache.

    Returns size and per-ecosystem subdirectory counts so operators can see
    what's accumulated. Useful before deciding whether to purge.
    """
    deps_root = Path(settings.skill_deps_path)
    if not deps_root.exists():
        return {
            "path": str(deps_root),
            "exists": False,
            "enabled": settings.enable_sandbox_network,
            "total_bytes": 0,
            "ecosystems": {},
        }

    def _dir_size(p: Path) -> int:
        total = 0
        for root, _dirs, files in os.walk(str(p)):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        return total

    ecosystems: Dict[str, Dict[str, Any]] = {}
    for sub in ("python", "node", "go", "cargo"):
        sp = deps_root / sub
        if sp.exists():
            ecosystems[sub] = {
                "exists": True,
                "bytes": _dir_size(sp),
            }
        else:
            ecosystems[sub] = {"exists": False, "bytes": 0}

    return {
        "path": str(deps_root),
        "exists": True,
        "enabled": settings.enable_sandbox_network,
        "total_bytes": _dir_size(deps_root),
        "ecosystems": ecosystems,
    }


@router.post("/skill-deps/purge", summary="Wipe the persistent skill-deps cache")
async def purge_skill_deps(_: str = Depends(verify_master_key)):
    """Delete every package the sandbox has installed.

    Use when the cache is bloated, when a bad install needs eviction, or
    after a suspected supply-chain incident. Next sandbox install cold-starts.
    The directory itself is recreated empty (sticky + world-writable) so
    sandboxes can immediately install fresh.
    """
    deps_root = Path(settings.skill_deps_path)
    if not deps_root.exists():
        return {"purged": True, "freed_bytes": 0, "path": str(deps_root)}

    freed = 0
    errors: List[str] = []
    try:
        for entry in deps_root.iterdir():
            try:
                if entry.is_dir() and not entry.is_symlink():
                    # Tally before nuking for the response.
                    for root, _dirs, files in os.walk(str(entry)):
                        for f in files:
                            try:
                                freed += os.path.getsize(os.path.join(root, f))
                            except OSError:
                                pass
                    shutil.rmtree(str(entry))
                else:
                    try:
                        freed += entry.stat().st_size
                    except OSError:
                        pass
                    entry.unlink()
            except OSError as exc:
                errors.append(f"{entry}: {exc}")
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not enumerate {deps_root}: {exc}",
        )

    # Reset perms so future installs from sandbox uids work.
    try:
        os.chmod(str(deps_root), 0o1777)  # nosec B103
    except OSError:
        pass

    logger.info(
        "Skill-deps cache purged",
        path=str(deps_root),
        freed_bytes=freed,
        errors=len(errors),
    )
    return {
        "purged": True,
        "path": str(deps_root),
        "freed_bytes": freed,
        "errors": errors,
    }
