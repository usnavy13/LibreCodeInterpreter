"""
Executor HTTP Service for Azure Container Apps.

FastAPI-based service that executes code in multiple languages
with sandboxed isolation. Runs as a separate container app
with internal-only ingress.
"""

import asyncio
import logging
import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from typing import Dict

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .models import ExecuteRequest, ExecuteResponse, HealthResponse
from .runner import execute_code
from .languages import get_supported_languages

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Configuration from environment
MAX_CONCURRENT_EXECUTIONS = int(os.getenv("MAX_CONCURRENT_EXECUTIONS", "4"))
EXECUTOR_PORT = int(os.getenv("EXECUTOR_PORT", "8001"))
WORKING_DIR_BASE = os.getenv("WORKING_DIR_BASE", "/mnt/data")

# Concurrency control
execution_semaphore: asyncio.Semaphore = None
active_executions: int = 0
execution_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global execution_semaphore

    logger.info(f"Starting executor service with max {MAX_CONCURRENT_EXECUTIONS} concurrent executions")

    # Initialize semaphore for concurrency control
    execution_semaphore = asyncio.Semaphore(MAX_CONCURRENT_EXECUTIONS)

    # Create working directory
    os.makedirs(WORKING_DIR_BASE, exist_ok=True)

    # Pre-import Python libraries in background
    logger.info("Pre-loading Python libraries...")
    try:
        from .python_state import PRELOADED_MODULES
        logger.info(f"Loaded {len(PRELOADED_MODULES)} Python modules")
    except Exception as e:
        logger.warning(f"Failed to pre-load some Python libraries: {e}")

    logger.info(f"Executor service ready. Supported languages: {', '.join(get_supported_languages())}")

    yield

    logger.info("Shutting down executor service")


app = FastAPI(
    title="Code Executor Service",
    description="Sandboxed code execution for Azure Container Apps",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint."""
    global active_executions

    return HealthResponse(
        status="healthy",
        version="1.0.0",
        languages=get_supported_languages(),
        concurrent_executions=active_executions,
        max_concurrent=MAX_CONCURRENT_EXECUTIONS,
    )


@app.post("/execute", response_model=ExecuteResponse)
async def execute(request: ExecuteRequest) -> ExecuteResponse:
    """
    Execute code in the specified language.

    Uses semaphore to limit concurrent executions and provides
    sandboxed isolation for security.
    """
    global active_executions

    # Generate unique execution ID
    execution_id = str(uuid.uuid4())[:8]
    logger.info(f"[{execution_id}] Executing {request.language} code ({len(request.code)} chars)")

    # Create unique working directory for this execution
    working_dir = os.path.join(WORKING_DIR_BASE, f"exec_{execution_id}")
    os.makedirs(working_dir, exist_ok=True)

    try:
        # Acquire semaphore to limit concurrent executions
        async with execution_semaphore:
            async with execution_lock:
                active_executions += 1

            try:
                result = await execute_code(
                    request=request,
                    working_dir=working_dir,
                )

                logger.info(
                    f"[{execution_id}] Completed: exit_code={result.exit_code}, "
                    f"time={result.execution_time_ms:.1f}ms, "
                    f"timed_out={result.timed_out}"
                )

                return result

            finally:
                async with execution_lock:
                    active_executions -= 1

    except Exception as e:
        logger.exception(f"[{execution_id}] Execution failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        # Cleanup working directory
        try:
            import shutil
            shutil.rmtree(working_dir, ignore_errors=True)
        except Exception:
            pass

        # Clean up files in parent /mnt/data created with absolute paths
        # This ensures isolation between executions
        try:
            for entry in os.scandir(WORKING_DIR_BASE):
                if entry.is_file():
                    try:
                        os.unlink(entry.path)
                        logger.debug(f"Cleaned up parent file: {entry.name}")
                    except Exception:
                        pass
        except Exception:
            pass


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler."""
    logger.exception(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "error": str(exc),
            "error_type": "internal_error",
        },
    )


# Allow running directly for development
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.executor.main:app",
        host="0.0.0.0",
        port=EXECUTOR_PORT,
        reload=True,
    )
