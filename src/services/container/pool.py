"""Container pool service for pre-warming containers.

This module provides a container pooling mechanism that:
1. Pre-warms containers per language for fast acquisition (~3ms vs 500-2000ms)
2. Provides fresh containers from the pool on demand
3. Does NOT track session-to-container mapping (stateless)

After execution, containers should be destroyed by the caller.
The pool continuously replenishes to maintain warm containers.
"""

import asyncio
from datetime import datetime
from typing import Dict, Optional, Set
import structlog

from docker.models.containers import Container

from ...config import settings
from ...models.pool import PooledContainer, PoolConfig, PoolStats
from ...core.events import (
    event_bus,
    ContainerAcquiredFromPool,
    ContainerCreatedFresh,
    PoolWarmedUp,
    PoolExhausted,
)
from .manager import ContainerManager
from .repl_executor import REPLExecutor

logger = structlog.get_logger(__name__)


class ContainerPool:
    """Container pool for fast container acquisition.

    Key behaviors:
    - Pre-warms containers per language based on configuration
    - Provides fresh containers from pool (O(1) acquisition)
    - Stateless: no session tracking (caller manages container lifecycle)
    - Continuously replenishes pool in background
    """

    def __init__(self, container_manager: ContainerManager):
        """Initialize the container pool.

        Args:
            container_manager: Manager for container lifecycle operations
        """
        self._container_manager = container_manager
        self._lock = asyncio.Lock()

        # Available containers per language (ready to be used)
        self._available: Dict[str, asyncio.Queue[PooledContainer]] = {}

        # Pool statistics per language
        self._stats: Dict[str, PoolStats] = {}

        # Background tasks
        self._warmup_task: Optional[asyncio.Task] = None
        self._running = False

        # Languages to warm up on startup
        self._warmup_languages: Set[str] = set()

        # Event for exhaustion-triggered replenishment
        self._replenish_event = asyncio.Event()

    async def start(self) -> None:
        """Start the container pool and warmup background task."""
        if self._running:
            return

        self._running = True
        logger.info("Starting container pool (simplified, no session tracking)")

        # Initialize queues for all supported languages and track those needing warmup
        all_languages = [
            "py",
            "js",
            "ts",
            "go",
            "java",
            "c",
            "cpp",
            "php",
            "rs",
            "r",
            "f90",
            "d",
        ]
        for lang in all_languages:
            self._available[lang] = asyncio.Queue()
            config = PoolConfig.from_settings(lang)
            if config.warmup_on_startup and config.size > 0:
                self._warmup_languages.add(lang)

        # Subscribe to exhaustion events for immediate replenishment
        if settings.container_pool_exhaustion_trigger:
            event_bus.register_handler(PoolExhausted, self._on_pool_exhausted)

        # Start warmup background task
        self._warmup_task = asyncio.create_task(self._warmup_loop())

        logger.info(
            "Container pool started",
            warmup_languages=list(self._warmup_languages),
            parallel_batch=settings.container_pool_parallel_batch,
            replenish_interval=settings.container_pool_replenish_interval,
            exhaustion_trigger=settings.container_pool_exhaustion_trigger,
        )

    async def stop(self) -> None:
        """Stop the container pool and cleanup all containers."""
        if not self._running:
            return

        self._running = False
        logger.info("Stopping container pool")

        # Cancel background task
        if self._warmup_task:
            self._warmup_task.cancel()
            try:
                await self._warmup_task
            except asyncio.CancelledError:
                pass

        # Destroy all pooled containers
        for lang, queue in self._available.items():
            count = 0
            while not queue.empty():
                try:
                    pooled = queue.get_nowait()
                    await self._destroy_container(pooled.container_id)
                    count += 1
                except asyncio.QueueEmpty:
                    break
            if count > 0:
                logger.info(f"Destroyed {count} pooled {lang} containers")

        logger.info("Container pool stopped")

    async def acquire(self, language: str, session_id: str = "") -> Container:
        """Acquire a container from the pool.

        This method:
        1. Gets a container from the pool if available
        2. Creates a new container if pool is empty

        Args:
            language: Programming language code
            session_id: Session identifier (for logging only, not tracked)

        Returns:
            Docker Container object ready for execution
        """
        start_time = datetime.utcnow()

        # Try to get from pool
        if settings.container_pool_enabled:
            queue = self._available.get(language)
            if queue and not queue.empty():
                try:
                    pooled = queue.get_nowait()
                    container = await self._get_docker_container(pooled.container_id)
                    if container and await self._is_container_healthy(container):
                        acquire_time = (
                            datetime.utcnow() - start_time
                        ).total_seconds() * 1000
                        await event_bus.publish(
                            ContainerAcquiredFromPool(
                                container_id=pooled.container_id,
                                session_id=session_id,
                                language=language,
                                acquire_time_ms=acquire_time,
                            )
                        )
                        self._record_stats(
                            language, pool_hit=True, acquire_time_ms=acquire_time
                        )
                        logger.info(
                            "Acquired container from pool",
                            session_id=session_id[:12] if session_id else "none",
                            container_id=pooled.container_id[:12],
                            language=language,
                            acquire_time_ms=f"{acquire_time:.1f}",
                        )
                        return container
                except asyncio.QueueEmpty:
                    pass

            # Pool empty
            await event_bus.publish(
                PoolExhausted(language=language, session_id=session_id)
            )

        # Create fresh container (fallback)
        container = await self._create_fresh_container(session_id, language)
        reason = "pool_empty" if settings.container_pool_enabled else "pool_disabled"
        await event_bus.publish(
            ContainerCreatedFresh(
                container_id=container.id,
                session_id=session_id,
                language=language,
                reason=reason,
            )
        )
        self._record_stats(language, pool_miss=True)

        return container

    async def destroy_container(self, container: Container) -> None:
        """Destroy a container after use.

        Call this after execution to clean up the container.
        Containers are never returned to the pool for security.
        """
        if container:
            await self._destroy_container(container.id)

    def get_stats(self, language: str = None) -> Dict[str, PoolStats]:
        """Get pool statistics."""
        if language:
            return {
                language: self._stats.get(
                    language,
                    PoolStats(language=language),
                )
            }

        # Build stats for all languages
        stats = {}
        for lang in set(list(self._available.keys()) + list(self._stats.keys())):
            queue = self._available.get(lang)
            available = queue.qsize() if queue else 0
            if lang in self._stats:
                self._stats[lang].available_count = available
                stats[lang] = self._stats[lang]
            else:
                stats[lang] = PoolStats(
                    language=lang, available_count=available
                )
        return stats

    # =========================================================================
    # Private methods
    # =========================================================================

    async def _create_fresh_container(
        self, session_id: str, language: str
    ) -> Container:
        """Create a new container when pool is exhausted."""
        image = self._container_manager.get_image_for_language(language)

        # Ensure image is available
        await self._container_manager.pull_image_if_needed(image)

        # Enable REPL mode for Python if configured (same as pooled containers)
        use_repl_mode = language == "py" and settings.repl_enabled

        # Create and start container
        container = self._container_manager.create_container(
            image=image,
            session_id=session_id,
            language=language,
            repl_mode=use_repl_mode,
        )

        started = await self._container_manager.start_container(container)
        if not started:
            try:
                container.remove(force=True)
            except Exception:
                pass
            raise RuntimeError(f"Failed to start container for {language}")

        # For REPL containers, wait for REPL to be ready before returning
        if use_repl_mode:
            repl_ready = await self._wait_for_repl_ready(container)
            if not repl_ready:
                logger.warning(
                    "REPL not ready in fresh container",
                    container_id=container.id[:12],
                    language=language,
                )

        logger.info(
            "Created fresh container",
            session_id=session_id[:12] if session_id else "none",
            container_id=container.id[:12],
            language=language,
            repl_mode=use_repl_mode,
        )

        return container

    async def _get_docker_container(self, container_id: str) -> Optional[Container]:
        """Get Docker container by ID."""
        try:
            return self._container_manager.client.containers.get(container_id)
        except Exception:
            return None

    async def _is_container_healthy(self, container: Container) -> bool:
        """Check if container is running and healthy."""
        try:
            container.reload()
            return container.status == "running"
        except Exception:
            return False

    async def _destroy_container(self, container_id: str) -> None:
        """Force remove a container."""
        try:
            container = await self._get_docker_container(container_id)
            if container:
                container.remove(force=True)
                logger.debug("Destroyed container", container_id=container_id[:12])
        except Exception as e:
            logger.warning(
                "Failed to destroy container",
                container_id=container_id[:12],
                error=str(e),
            )

    async def _warmup_loop(self) -> None:
        """Background task to maintain warm containers in the pool."""
        # Initial warmup
        await asyncio.sleep(2)  # Let the app start

        replenish_interval = settings.container_pool_replenish_interval

        while self._running:
            try:
                for language in self._warmup_languages:
                    await self._warmup_language(language)

                # Wait for either timeout OR exhaustion event (if enabled)
                if settings.container_pool_exhaustion_trigger:
                    try:
                        await asyncio.wait_for(
                            self._replenish_event.wait(),
                            timeout=float(replenish_interval),
                        )
                        # Event was triggered - immediate replenishment
                        self._replenish_event.clear()
                        logger.debug("Exhaustion-triggered replenishment")
                    except asyncio.TimeoutError:
                        pass  # Normal timeout, continue loop
                else:
                    await asyncio.sleep(replenish_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Warmup loop error", error=str(e))
                await asyncio.sleep(replenish_interval)

    async def _on_pool_exhausted(self, event: PoolExhausted) -> None:
        """Handle pool exhaustion event by triggering immediate replenishment."""
        logger.info(
            "Pool exhaustion detected, triggering replenishment",
            language=event.language,
            session_id=event.session_id[:12] if event.session_id else "none",
        )
        self._replenish_event.set()

    async def _warmup_language(self, language: str) -> None:
        """Warm up containers for a specific language using parallel creation."""
        config = PoolConfig.from_settings(language)
        queue = self._available.setdefault(language, asyncio.Queue())

        current_size = queue.qsize()
        if current_size >= config.size:
            return

        needed = config.size - current_size
        created = 0

        # Enable REPL mode for Python if configured
        use_repl_mode = language == "py" and settings.repl_enabled

        # Parallel container creation in batches
        batch_size = settings.container_pool_parallel_batch

        for batch_start in range(0, needed, batch_size):
            batch_end = min(batch_start + batch_size, needed)
            batch_count = batch_end - batch_start

            # Launch container creations in parallel
            tasks = [
                self._create_pooled_container(language, use_repl_mode)
                for _ in range(batch_count)
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, PooledContainer):
                    await queue.put(result)
                    created += 1
                elif isinstance(result, Exception):
                    logger.warning(
                        "Failed to create pooled container",
                        language=language,
                        error=str(result),
                    )

        if created > 0:
            await event_bus.publish(
                PoolWarmedUp(language=language, container_count=created)
            )
            logger.info(
                "Warmed up containers (parallel)",
                language=language,
                created=created,
                total=queue.qsize(),
                repl_mode=use_repl_mode,
                batch_size=batch_size,
            )

    async def _create_pooled_container(
        self, language: str, use_repl_mode: bool
    ) -> Optional[PooledContainer]:
        """Create a single pooled container (for parallel execution).

        Args:
            language: Programming language code
            use_repl_mode: Whether to enable REPL mode (Python only)

        Returns:
            PooledContainer if successful, None if failed
        """
        import uuid

        try:
            image = self._container_manager.get_image_for_language(language)
            await self._container_manager.pull_image_if_needed(image)

            # Create container with a unique pool-specific session ID
            pool_session_id = f"pool-{language}-{uuid.uuid4().hex[:12]}"
            container = self._container_manager.create_container(
                image=image,
                session_id=pool_session_id,
                language=language,
                repl_mode=use_repl_mode,
            )

            started = await self._container_manager.start_container(container)
            if not started:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
                return None

            # For REPL containers, wait for REPL to be ready
            repl_ready = True
            if use_repl_mode:
                repl_ready = await self._wait_for_repl_ready(container)
                if not repl_ready:
                    logger.warning(
                        "REPL not ready, removing container",
                        container_id=container.id[:12],
                        language=language,
                    )
                    try:
                        container.remove(force=True)
                    except Exception:
                        pass
                    return None

            pooled = PooledContainer(
                container_id=container.id,
                language=language,
                image=image,
                created_at=datetime.utcnow(),
                status="available",
                repl_enabled=use_repl_mode,
                repl_ready=repl_ready if use_repl_mode else False,
            )

            if use_repl_mode:
                logger.debug(
                    "REPL container ready",
                    container_id=container.id[:12],
                    language=language,
                )

            return pooled

        except Exception as e:
            logger.warning(
                "Failed to create pooled container", language=language, error=str(e)
            )
            return None

    async def _wait_for_repl_ready(
        self, container: Container, timeout: float = 15.0
    ) -> bool:
        """Wait for REPL server to be ready in container.

        Args:
            container: Container with REPL server
            timeout: Maximum time to wait in seconds

        Returns:
            True if REPL is ready, False if timeout
        """
        try:
            repl_executor = REPLExecutor(self._container_manager.client)
            return await repl_executor.wait_for_ready(container, timeout=timeout)
        except Exception as e:
            logger.warning(
                "Error waiting for REPL ready",
                container_id=container.id[:12],
                error=str(e),
            )
            return False

    def _record_stats(
        self,
        language: str,
        pool_hit: bool = False,
        pool_miss: bool = False,
        acquire_time_ms: float = 0.0,
    ) -> None:
        """Record pool statistics."""
        if language not in self._stats:
            self._stats[language] = PoolStats(
                language=language
            )

        stats = self._stats[language]
        stats.total_acquisitions += 1

        if pool_hit:
            stats.pool_hits += 1
        if pool_miss:
            stats.pool_misses += 1
        if acquire_time_ms > 0:
            # Running average
            n = stats.total_acquisitions
            stats.avg_acquire_time_ms = (
                stats.avg_acquire_time_ms * (n - 1) + acquire_time_ms
            ) / n
