"""Main FastAPI application for the Code Interpreter API."""

# Standard library imports
import sys
from contextlib import asynccontextmanager

# Third-party imports
import structlog
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import ValidationError

# Local application imports
from .api import files, exec, health, admin, dashboard_metrics
from .config import settings
from .middleware.security import SecurityMiddleware, RequestLoggingMiddleware
from .middleware.metrics import MetricsMiddleware
from .models.errors import CodeInterpreterException
from .services.health import health_service
from .services.metrics import metrics_service
from .utils.config_validator import validate_configuration, get_configuration_summary
from .utils.error_handlers import (
    code_interpreter_exception_handler,
    http_exception_handler,
    validation_exception_handler,
    general_exception_handler,
)
from .utils.logging import setup_logging
from .utils.shutdown import setup_graceful_shutdown, shutdown_handler

# Setup logging
setup_logging()
logger = structlog.get_logger()


async def _startup_monitoring(app: FastAPI) -> None:
    """Start metrics and monitoring services."""
    try:
        await metrics_service.start()
        metrics_service.register_event_handlers()
        logger.info("Metrics service started")
    except Exception as e:
        logger.error("Failed to start metrics service", error=str(e))


async def _startup_cleanup_tasks() -> None:
    """Start session cleanup and event-driven cleanup scheduler."""
    try:
        from .dependencies.services import get_session_service

        session_service = get_session_service()
        await session_service.start_cleanup_task()
        logger.info("Session cleanup task started")
    except Exception as e:
        logger.error("Failed to start session cleanup task", error=str(e))

    try:
        from .services.cleanup import cleanup_scheduler
        from .dependencies.services import (
            get_execution_service,
            get_file_service,
            get_state_archival_service,
        )

        cleanup_scheduler.set_services(
            execution_service=get_execution_service(),
            file_service=get_file_service(),
            state_archival_service=(
                get_state_archival_service() if settings.state_archive_enabled else None
            ),
        )
        cleanup_scheduler.start()
        logger.info("Cleanup scheduler started")
    except Exception as e:
        logger.error("Failed to start cleanup scheduler", error=str(e))


async def _startup_sandbox_pool(app: FastAPI) -> None:
    """Start the sandbox pool if enabled."""
    if settings.sandbox_pool_enabled:
        try:
            from .services.sandbox.pool import SandboxPool
            from .services.sandbox.manager import SandboxManager
            from .services.cleanup import cleanup_scheduler
            from .dependencies.services import (
                set_sandbox_pool,
                inject_sandbox_pool_to_execution_service,
            )

            sandbox_manager = SandboxManager()
            sandbox_pool = SandboxPool(sandbox_manager)
            await sandbox_pool.start()

            # Connect pool to cleanup scheduler
            cleanup_scheduler.set_sandbox_pool(sandbox_pool)

            # Register pool with dependency injection system
            set_sandbox_pool(sandbox_pool)
            inject_sandbox_pool_to_execution_service()

            # Register pool with health service for monitoring
            health_service.set_sandbox_pool(sandbox_pool)

            # Store pool reference in app state
            app.state.sandbox_pool = sandbox_pool

            logger.info("Sandbox pool started")
        except Exception as e:
            logger.error("Failed to start sandbox pool", error=str(e))
    else:
        logger.info("Sandbox pool disabled")


async def _perform_health_checks() -> None:
    """Perform initial health checks on all services."""
    try:
        health_results = await health_service.check_all_services(use_cache=False)

        for service_name, result in health_results.items():
            if result.status.value == "healthy":
                logger.debug(
                    f"{service_name} healthy",
                    response_time_ms=result.response_time_ms,
                )
            else:
                logger.warning(
                    f"{service_name} health check failed",
                    status=result.status.value,
                    error=result.error,
                )

        overall_status = health_service.get_overall_status(health_results)
        logger.info("Health checks completed", overall_status=overall_status.value)
    except Exception as e:
        logger.error("Initial health checks failed", error=str(e))


async def _shutdown_services(app: FastAPI) -> None:
    """Stop monitoring services, sandbox pool, and cleanup scheduler."""
    try:
        await metrics_service.stop()
        logger.info("Metrics service stopped")
    except Exception as e:
        logger.error("Error stopping metrics service", error=str(e))

    if hasattr(app.state, "sandbox_pool") and app.state.sandbox_pool:
        try:
            await app.state.sandbox_pool.stop()
            logger.info("Sandbox pool stopped")
        except Exception as e:
            logger.error("Error stopping sandbox pool", error=str(e))

    try:
        from .services.cleanup import cleanup_scheduler

        cleanup_scheduler.stop()
        logger.info("Cleanup scheduler stopped")
    except Exception as e:
        logger.error("Error stopping cleanup scheduler", error=str(e))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    logger.info("Starting Code Interpreter API", version="1.0.0")

    setup_graceful_shutdown()

    if not validate_configuration():
        logger.error("Configuration validation failed - shutting down")
        sys.exit(1)

    if settings.api_key == "test-api-key":
        logger.warning("Using default API key - CHANGE THIS IN PRODUCTION!")
    if settings.api_debug:
        logger.warning("Debug mode is enabled - disable in production")
    if settings.master_api_key:
        logger.info("API key management enabled")
    logger.debug("Rate limiting", enabled=settings.rate_limit_enabled)

    await _startup_monitoring(app)
    await _startup_cleanup_tasks()
    await _startup_sandbox_pool(app)
    await _perform_health_checks()

    logger.info("Code Interpreter API startup completed")

    yield

    logger.info("Shutting down Code Interpreter API")

    await _shutdown_services(app)

    try:
        await shutdown_handler.shutdown()
    except Exception as e:
        logger.error("Error during graceful shutdown", error=str(e))

    logger.info("Code Interpreter API shutdown completed")


# Create FastAPI app with enhanced configuration
app = FastAPI(
    title="Code Interpreter API",
    description="A secure API for executing code in isolated environments",
    version="1.0.0",
    docs_url="/docs" if settings.enable_docs else None,
    redoc_url="/redoc" if settings.enable_docs else None,
    debug=settings.api_debug,
    lifespan=lifespan,
)

# Add middleware (order matters - most specific first)
app.add_middleware(MetricsMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(SecurityMiddleware)

# Add CORS middleware (conditionally)
if settings.enable_cors:
    origins = settings.cors_origins if settings.cors_origins else ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=[
            "Content-Disposition"
        ],  # Removed Content-Length for chunked encoding
    )
    logger.info("CORS enabled", origins=origins)

# Register global error handlers
app.add_exception_handler(CodeInterpreterException, code_interpreter_exception_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(ValidationError, validation_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)


@app.get("/config")
async def config_info():
    """Configuration information endpoint (non-sensitive data only)."""
    if not settings.api_debug:
        raise HTTPException(status_code=404, detail="Not found")

    return get_configuration_summary()


# Include routers (authentication handled by middleware)
# Files routes - mount without prefix for LibreChat compatibility
app.include_router(files.router, tags=["files"])

app.include_router(exec.router, tags=["exec"])

app.include_router(health.router, tags=["health", "monitoring"])

app.include_router(admin.router, prefix="/api/v1", tags=["admin"])

app.include_router(dashboard_metrics.router, prefix="/api/v1", tags=["admin-metrics"])

# Admin Dashboard Frontend
app.mount(
    "/admin-dashboard/static",
    StaticFiles(directory="dashboard/static"),
    name="dashboard-static",
)


@app.get("/admin-dashboard", tags=["admin"])
async def get_admin_dashboard():
    """Serve the admin dashboard frontend."""
    return FileResponse("dashboard/index.html")


@app.get("/admin-dashboard/{rest_of_path:path}", tags=["admin"])
async def get_admin_dashboard_deep_link(rest_of_path: str):
    """Handle deep links for the admin dashboard by serving index.html."""
    return FileResponse("dashboard/index.html")


def run_server():
    if settings.https_enabled:
        # Validate SSL files exist
        if not settings.validate_ssl_files():
            logger.error("SSL configuration invalid - missing certificate files")
            sys.exit(1)

        # Configure SSL
        ssl_config = {
            "ssl_certfile": settings.ssl_cert_file,
            "ssl_keyfile": settings.ssl_key_file,
        }
        if settings.ssl_ca_certs:
            ssl_config["ssl_ca_certs"] = settings.ssl_ca_certs

        logger.info(f"Starting HTTPS server on {settings.api_host}:{settings.api_port}")
        uvicorn.run(
            "src.main:app",
            host=settings.api_host,
            port=settings.api_port,
            reload=settings.api_reload,
            log_level=settings.log_level.lower(),
            access_log=settings.enable_access_logs,
            timeout_keep_alive=120,
            **ssl_config,
        )
    else:
        logger.info(f"Starting HTTP server on {settings.api_host}:{settings.api_port}")
        uvicorn.run(
            "src.main:app",
            host=settings.api_host,
            port=settings.api_port,
            reload=settings.api_reload,
            log_level=settings.log_level.lower(),
            access_log=settings.enable_access_logs,
            timeout_keep_alive=120,
        )


if __name__ == "__main__":
    run_server()
