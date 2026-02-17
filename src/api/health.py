"""Health check and monitoring endpoints."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import JSONResponse
import structlog

from ..services.health import health_service, HealthStatus
from ..services.metrics import metrics_service
from ..dependencies.auth import verify_api_key
from ..config import settings

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.get("/health", summary="Basic health check")
async def basic_health_check():
    """Basic health check endpoint that doesn't require authentication."""
    return {
        "status": "healthy",
        "version": "1.0.0",
        "timestamp": "2025-01-18T00:00:00Z",
        "service": "code-interpreter-api",
    }


@router.get("/health/detailed", summary="Detailed health check")
async def detailed_health_check(
    use_cache: bool = Query(True, description="Use cached health check results"),
    _: str = Depends(verify_api_key),
):
    """Detailed health check of all system dependencies."""
    try:
        # Get health check results for all services
        service_results = await health_service.check_all_services(use_cache=use_cache)

        # Determine overall status
        overall_status = health_service.get_overall_status(service_results)

        # Prepare response
        response_data = {
            "status": overall_status.value,
            "timestamp": (
                service_results[list(service_results.keys())[0]].timestamp.isoformat()
                if service_results
                else None
            ),
            "services": {
                name: result.to_dict() for name, result in service_results.items()
            },
            "summary": {
                "total_services": len(service_results),
                "healthy_services": sum(
                    1
                    for r in service_results.values()
                    if r.status == HealthStatus.HEALTHY
                ),
                "degraded_services": sum(
                    1
                    for r in service_results.values()
                    if r.status == HealthStatus.DEGRADED
                ),
                "unhealthy_services": sum(
                    1
                    for r in service_results.values()
                    if r.status == HealthStatus.UNHEALTHY
                ),
            },
        }

        # Set appropriate HTTP status code
        if overall_status == HealthStatus.UNHEALTHY:
            return JSONResponse(status_code=503, content=response_data)
        elif overall_status == HealthStatus.DEGRADED:
            return JSONResponse(
                status_code=200,
                content=response_data,
                headers={"X-Health-Status": "degraded"},
            )
        else:
            return JSONResponse(status_code=200, content=response_data)

    except Exception as e:
        logger.error("Health check failed", error=str(e))
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "error": "Health check system failure",
                "details": str(e) if settings.api_debug else "Internal error",
            },
        )


@router.get("/health/redis", summary="Redis health check")
async def redis_health_check(_: str = Depends(verify_api_key)):
    """Check Redis connectivity and performance."""
    try:
        result = await health_service.check_redis()

        if result.status == HealthStatus.UNHEALTHY:
            return JSONResponse(status_code=503, content=result.to_dict())
        else:
            return JSONResponse(status_code=200, content=result.to_dict())

    except Exception as e:
        logger.error("Redis health check failed", error=str(e))
        return JSONResponse(
            status_code=503,
            content={
                "service": "redis",
                "status": "unhealthy",
                "error": str(e) if settings.api_debug else "Redis check failed",
            },
        )


@router.get("/health/minio", summary="MinIO health check")
async def minio_health_check(_: str = Depends(verify_api_key)):
    """Check MinIO/S3 connectivity and performance."""
    try:
        result = await health_service.check_minio()

        if result.status == HealthStatus.UNHEALTHY:
            return JSONResponse(status_code=503, content=result.to_dict())
        else:
            return JSONResponse(status_code=200, content=result.to_dict())

    except Exception as e:
        logger.error("MinIO health check failed", error=str(e))
        return JSONResponse(
            status_code=503,
            content={
                "service": "minio",
                "status": "unhealthy",
                "error": str(e) if settings.api_debug else "MinIO check failed",
            },
        )


@router.get("/health/nsjail", summary="nsjail health check")
async def nsjail_health_check(_: str = Depends(verify_api_key)):
    """Check nsjail sandbox availability and configuration."""
    try:
        result = await health_service.check_nsjail()

        if result.status == HealthStatus.UNHEALTHY:
            return JSONResponse(status_code=503, content=result.to_dict())
        else:
            return JSONResponse(status_code=200, content=result.to_dict())

    except Exception as e:
        logger.error("nsjail health check failed", error=str(e))
        return JSONResponse(
            status_code=503,
            content={
                "service": "nsjail",
                "status": "unhealthy",
                "error": str(e) if settings.api_debug else "nsjail check failed",
            },
        )


@router.get("/metrics", summary="System metrics")
async def get_metrics(_: str = Depends(verify_api_key)):
    """Get system metrics and statistics."""
    try:
        return {
            "execution_statistics": metrics_service.get_execution_statistics(),
            "api_statistics": metrics_service.get_api_statistics(),
            "system_metrics": metrics_service.get_system_metrics(),
        }

    except Exception as e:
        logger.error("Failed to get metrics", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to retrieve metrics")


@router.get("/metrics/execution", summary="Execution metrics")
async def get_execution_metrics(_: str = Depends(verify_api_key)):
    """Get code execution metrics and statistics."""
    try:
        return metrics_service.get_execution_statistics()

    except Exception as e:
        logger.error("Failed to get execution metrics", error=str(e))
        raise HTTPException(
            status_code=500, detail="Failed to retrieve execution metrics"
        )


@router.get("/metrics/api", summary="API metrics")
async def get_api_metrics(_: str = Depends(verify_api_key)):
    """Get API request metrics and statistics."""
    try:
        return metrics_service.get_api_statistics()

    except Exception as e:
        logger.error("Failed to get API metrics", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to retrieve API metrics")


@router.get("/status", summary="Service status")
async def get_service_status(_: str = Depends(verify_api_key)):
    """Get comprehensive service status information."""
    try:
        # Get health check results
        service_results = await health_service.check_all_services(use_cache=True)
        overall_status = health_service.get_overall_status(service_results)

        # Get basic metrics
        system_metrics = metrics_service.get_system_metrics()

        return {
            "overall_status": overall_status.value,
            "services": {
                name: {
                    "status": result.status.value,
                    "response_time_ms": result.response_time_ms,
                    "last_check": result.timestamp.isoformat(),
                }
                for name, result in service_results.items()
            },
            "metrics": {
                "total_executions": system_metrics.get("counters", {}).get(
                    "executions_total", 0
                ),
                "total_api_requests": system_metrics.get("counters", {}).get(
                    "api_requests_total", 0
                ),
                "buffer_size": system_metrics.get("buffer_size", 0),
                "uptime_seconds": system_metrics.get("uptime_seconds", 0),
            },
            "configuration": {
                "debug_mode": settings.api_debug,
                "max_execution_time": settings.max_execution_time,
                "max_memory_mb": settings.max_memory_mb,
                "session_ttl_hours": settings.session_ttl_hours,
                "supported_languages": list(settings.supported_languages.keys()),
            },
        }

    except Exception as e:
        logger.error("Failed to get service status", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to retrieve service status")


@router.get("/metrics/detailed", summary="Detailed execution metrics")
async def get_detailed_metrics(
    hours: int = Query(24, ge=1, le=168, description="Number of hours to include"),
    _: str = Depends(verify_api_key),
):
    """Get detailed execution metrics with per-language and per-key breakdown.

    Returns:
        Summary metrics, language breakdown, and pool statistics
    """
    try:
        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=hours)

        summary = await metrics_service.get_summary_stats(start=start, end=now)
        top_langs = await metrics_service.get_top_languages(
            start=start, end=now, limit=10
        )
        pool_stats = metrics_service.get_pool_stats()

        return {
            "summary": summary,
            "by_language": {lang["language"]: lang for lang in top_langs},
            "pool_stats": pool_stats,
            "period_hours": hours,
        }

    except Exception as e:
        logger.error("Failed to get detailed metrics", error=str(e))
        raise HTTPException(
            status_code=500, detail="Failed to retrieve detailed metrics"
        )


@router.get("/metrics/by-language", summary="Per-language metrics")
async def get_language_metrics(
    hours: int = Query(24, ge=1, le=168, description="Number of hours to include"),
    _: str = Depends(verify_api_key),
):
    """Get execution metrics broken down by programming language.

    Returns:
        Execution counts, average times, and error rates per language
    """
    try:
        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=hours)

        lang_data = await metrics_service.get_language_usage(start=start, end=now)
        by_language = lang_data.get("by_language", {})

        languages = [
            {"language": lang, "execution_count": count}
            for lang, count in sorted(
                by_language.items(), key=lambda x: x[1], reverse=True
            )
        ]

        return {
            "languages": languages,
            "period_hours": hours,
            "total_languages": len(languages),
        }

    except Exception as e:
        logger.error("Failed to get language metrics", error=str(e))
        raise HTTPException(
            status_code=500, detail="Failed to retrieve language metrics"
        )


@router.get("/metrics/by-api-key/{key_hash}", summary="Per-API-key metrics")
async def get_api_key_metrics(
    key_hash: str,
    hours: int = Query(24, ge=1, le=168, description="Number of hours to include"),
    _: str = Depends(verify_api_key),
):
    """Get execution metrics for a specific API key.

    Args:
        key_hash: First 16 characters of the API key hash

    Returns:
        Execution counts, success rates, and resource usage for the key
    """
    try:
        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=hours)

        stats = await metrics_service.get_summary_stats(
            start=start, end=now, api_key_hash=key_hash
        )

        return {
            "api_key_hash": key_hash,
            "stats": stats,
            "period_hours": hours,
        }

    except Exception as e:
        logger.error("Failed to get API key metrics", error=str(e))
        raise HTTPException(
            status_code=500, detail="Failed to retrieve API key metrics"
        )


@router.get("/metrics/pool", summary="Sandbox pool metrics")
async def get_pool_metrics(_: str = Depends(verify_api_key)):
    """Get sandbox pool statistics.

    Returns:
        Pool hit rates, acquisition times, and exhaustion events
    """
    try:
        return metrics_service.get_pool_stats()

    except Exception as e:
        logger.error("Failed to get pool metrics", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to retrieve pool metrics")
