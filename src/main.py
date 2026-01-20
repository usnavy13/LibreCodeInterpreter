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
from .api import files, exec, health, state, admin, dashboard_metrics
from .config import settings
from .middleware.security import SecurityMiddleware, RequestLoggingMiddleware
from .middleware.metrics import MetricsMiddleware
from .models.errors import CodeInterpreterException
from .services.health import health_service
from .services.metrics import metrics_collector
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    logger.info("Starting Code Interpreter API", version="1.0.0")

    # Setup graceful shutdown callbacks (uvicorn handles signals)
    setup_graceful_shutdown()

    # Validate configuration on startup
    if not validate_configuration():
        logger.error("Configuration validation failed - shutting down")
        sys.exit(1)

    # Log security warnings if applicable
    if settings.api_key == "test-api-key":
        logger.warning("Using default API key - CHANGE THIS IN PRODUCTION!")

    if settings.api_debug:
        logger.warning("Debug mode is enabled - disable in production")

    # Log API key management status
    if settings.master_api_key:
        logger.info("API key management enabled (MASTER_API_KEY configured)")
    else:
        logger.info("API key management: CLI disabled (no MASTER_API_KEY set)")

    logger.info(
        "Rate limiting configuration", rate_limit_enabled=settings.rate_limit_enabled
    )

    # Start monitoring services
    try:
        logger.info("Starting metrics collector...")
        await metrics_collector.start()
        logger.info("Metrics collector started successfully")
    except Exception as e:
        logger.error("Failed to start metrics collector", error=str(e))
        # Don't fail startup if metrics collector fails

    # Start SQLite metrics service for long-term analytics
    if settings.sqlite_metrics_enabled:
        try:
            logger.info("Starting SQLite metrics service...")
            from .services.sqlite_metrics import sqlite_metrics_service

            await sqlite_metrics_service.start()
            app.state.sqlite_metrics_service = sqlite_metrics_service
            logger.info(
                "SQLite metrics service started successfully",
                db_path=settings.sqlite_metrics_db_path,
            )
        except Exception as e:
            logger.error("Failed to start SQLite metrics service", error=str(e))
            # Don't fail startup if SQLite metrics fails

    # Start session cleanup task
    try:
        logger.info("Starting session cleanup task...")
        from .dependencies.services import get_session_service

        session_service = get_session_service()
        await session_service.start_cleanup_task()
        logger.info("Session cleanup task started successfully")
    except Exception as e:
        logger.error("Failed to start session cleanup task", error=str(e))
        # Don't fail startup if cleanup task fails

    # Start event-driven cleanup scheduler
    try:
        logger.info("Starting cleanup scheduler...")
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
        logger.info(
            "Cleanup scheduler started successfully",
            state_archival_enabled=settings.state_archive_enabled,
        )
    except Exception as e:
        logger.error("Failed to start cleanup scheduler", error=str(e))
        # Don't fail startup if cleanup scheduler fails

    # Initialize WAN network for container internet access if enabled
    # IMPORTANT: This must happen BEFORE the container pool starts
    if settings.enable_wan_access:
        try:
            logger.info("Initializing WAN network for container internet access...")
            from .services.container.network import WANNetworkManager
            from .services.container.manager import ContainerManager

            temp_manager = ContainerManager()
            if temp_manager.is_available():
                wan_network_manager = WANNetworkManager(temp_manager.client)
                if await wan_network_manager.initialize():
                    app.state.wan_network_manager = wan_network_manager
                    logger.info(
                        "WAN network initialized successfully",
                        network_name=settings.wan_network_name,
                        dns_servers=settings.wan_dns_servers,
                    )
                else:
                    logger.error("Failed to initialize WAN network")
            else:
                logger.warning("Docker not available, skipping WAN network setup")
        except Exception as e:
            logger.error("Error initializing WAN network", error=str(e))
            # Don't fail startup if WAN network fails
    else:
        logger.info("WAN network access disabled (containers have no network access)")

    # Start container pool if enabled
    container_pool = None
    if settings.container_pool_enabled:
        try:
            logger.info("Starting container pool...")
            from .services.container.pool import ContainerPool
            from .services.container.manager import ContainerManager
            from .services.cleanup import cleanup_scheduler
            from .dependencies.services import (
                set_container_pool,
                inject_container_pool_to_execution_service,
            )

            container_manager = ContainerManager()
            container_pool = ContainerPool(container_manager)
            await container_pool.start()

            # Connect pool to cleanup scheduler
            cleanup_scheduler.set_container_pool(container_pool)

            # Register pool with dependency injection system
            set_container_pool(container_pool)
            inject_container_pool_to_execution_service()

            # Register pool with health service for monitoring
            health_service.set_container_pool(container_pool)

            # Store pool reference in app state
            app.state.container_pool = container_pool

            logger.info(
                "Container pool started successfully",
                warmup_languages=["py", "js", "ts", "go", "java"],
            )
        except Exception as e:
            logger.error("Failed to start container pool", error=str(e))
            # Don't fail startup if container pool fails
            container_pool = None
    else:
        logger.info("Container pool disabled by configuration")

    # Perform initial health checks
    try:
        logger.info("Performing initial health checks...")
        health_results = await health_service.check_all_services(use_cache=False)

        # Log health check results
        for service_name, result in health_results.items():
            if result.status.value == "healthy":
                logger.info(
                    f"{service_name} health check passed",
                    response_time_ms=result.response_time_ms,
                )
            else:
                logger.warning(
                    f"{service_name} health check failed",
                    status=result.status.value,
                    error=result.error,
                )

        overall_status = health_service.get_overall_status(health_results)
        logger.info(
            "Initial health checks completed", overall_status=overall_status.value
        )

    except Exception as e:
        logger.error("Initial health checks failed", error=str(e))
        # Don't fail startup if health checks fail

    logger.info("Code Interpreter API startup completed")

    yield

    # Shutdown
    logger.info("Shutting down Code Interpreter API")

    # Cleanup WAN network iptables rules
    if hasattr(app.state, "wan_network_manager") and app.state.wan_network_manager:
        try:
            await app.state.wan_network_manager.cleanup()
            logger.info("WAN network iptables rules cleaned up")
        except Exception as e:
            logger.error("Error cleaning up WAN network", error=str(e))

    # Stop SQLite metrics service (flush pending writes)
    if (
        hasattr(app.state, "sqlite_metrics_service")
        and app.state.sqlite_metrics_service
    ):
        try:
            await app.state.sqlite_metrics_service.stop()
            logger.info("SQLite metrics service stopped")
        except Exception as e:
            logger.error("Error stopping SQLite metrics service", error=str(e))

    # Stop container pool first (it manages active containers)
    if hasattr(app.state, "container_pool") and app.state.container_pool:
        try:
            await app.state.container_pool.stop()
            logger.info("Container pool stopped")
        except Exception as e:
            logger.error("Error stopping container pool", error=str(e))

    # Stop cleanup scheduler
    try:
        from .services.cleanup import cleanup_scheduler

        cleanup_scheduler.stop()
        logger.info("Cleanup scheduler stopped")
    except Exception as e:
        logger.error("Error stopping cleanup scheduler", error=str(e))

    # Perform graceful shutdown
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


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": "1.0.0",
        "config": {
            "debug": settings.api_debug,
            "docs_enabled": settings.enable_docs,
            "cors_enabled": settings.enable_cors,
        },
    }


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

app.include_router(state.router, tags=["state"])

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
    if settings.enable_https:
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

        logger.info(
            f"Starting HTTPS server on {settings.api_host}:{settings.https_port}"
        )
        uvicorn.run(
            "src.main:app",
            host=settings.api_host,
            port=settings.https_port,
            reload=settings.api_reload,
            log_level=settings.log_level.lower(),
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
        )


if __name__ == "__main__":
    run_server()
