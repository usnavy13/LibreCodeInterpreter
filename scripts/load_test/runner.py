"""Test execution orchestrator for load testing."""

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
import uuid

from .client import LoadTestClient
from .config import DEFAULTS
from .models import (
    ConcurrencyTestResult,
    ExecutionResult,
    LoadTestConfig,
    LoadTestReport,
    ScenarioSummary,
    SystemMetrics,
)
from .monitor import ResourceMonitor
from .scenarios.base import BaseScenario


class LoadTestRunner:
    """Orchestrates load test execution."""

    def __init__(
        self,
        config: LoadTestConfig,
        progress_callback: Optional[Callable[[str], None]] = None,
    ):
        self.config = config
        self.client = LoadTestClient(
            base_url=config.base_url,
            api_key=config.api_key,
            timeout_seconds=config.timeout_seconds,
            max_connections=config.max_concurrency * 2,
        )
        self.monitor = ResourceMonitor(
            sample_interval=config.monitor_interval_seconds,
            enable_sandbox_stats=config.enable_sandbox_stats,
        )
        self.progress_callback = progress_callback or (lambda x: None)

    async def __aenter__(self) -> "LoadTestRunner":
        await self.client.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.client.close()

    def _log(self, message: str) -> None:
        """Log progress message."""
        self.progress_callback(message)

    async def warmup(self, language: str = "py") -> bool:
        """Warm up the API before testing."""
        self._log(f"Warming up API with {self.config.warmup_requests} requests...")
        successful = await self.client.warmup(language, self.config.warmup_requests)
        success = successful == self.config.warmup_requests
        self._log(f"Warmup complete: {successful}/{self.config.warmup_requests} successful")
        return success

    async def run_scenario_at_concurrency(
        self,
        scenario: BaseScenario,
        concurrency: int,
        num_requests: int,
    ) -> ConcurrencyTestResult:
        """Run a scenario at a specific concurrency level."""
        result = ConcurrencyTestResult(
            concurrency=concurrency,
            scenario_id=scenario.id,
        )

        # Start monitoring
        await self.monitor.start()
        start_time = time.perf_counter()

        # Create semaphore for concurrency control
        semaphore = asyncio.Semaphore(concurrency)

        async def bounded_request(iteration: int) -> ExecutionResult:
            async with semaphore:
                code = scenario.get_code(iteration)
                return await self.client.execute_code(
                    code=code,
                    language=scenario.language,
                    scenario_id=scenario.id,
                )

        # Execute all requests concurrently
        tasks = [bounded_request(i) for i in range(num_requests)]
        execution_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Stop monitoring
        end_time = time.perf_counter()
        system_metrics = await self.monitor.stop()
        sandbox_stats = self.monitor.get_sandbox_summary()

        # Process results
        for exec_result in execution_results:
            result.total_requests += 1
            if isinstance(exec_result, Exception):
                result.failed_requests += 1
                error_key = str(exec_result)[:50]
                result.errors[error_key] = result.errors.get(error_key, 0) + 1
            elif exec_result.success:
                result.successful_requests += 1
                result.latencies.append(exec_result.latency_ms)
            else:
                result.failed_requests += 1
                error_key = (exec_result.error or "Unknown error")[:50]
                result.errors[error_key] = result.errors.get(error_key, 0) + 1

        result.duration_seconds = end_time - start_time
        result.system_metrics = system_metrics
        result.sandbox_stats = sandbox_stats

        return result

    async def run_ramp_up_test(
        self,
        scenario: BaseScenario,
        min_concurrency: Optional[int] = None,
        max_concurrency: Optional[int] = None,
        step: Optional[int] = None,
        requests_per_step: Optional[int] = None,
    ) -> ScenarioSummary:
        """Run ramp-up test to find breaking point."""
        min_c = min_concurrency or self.config.min_concurrency
        max_c = max_concurrency or self.config.max_concurrency
        step_size = step or self.config.concurrency_step
        reqs = requests_per_step or self.config.requests_per_step

        self._log(f"Running ramp-up test for {scenario.name}")
        self._log(f"  Concurrency: {min_c} to {max_c} (step {step_size})")
        self._log(f"  Requests per level: {reqs}")

        summary = ScenarioSummary(
            scenario_id=scenario.id,
            scenario_name=scenario.name,
            category=scenario.category,
            language=scenario.language,
        )

        current_concurrency = min_c
        while current_concurrency <= max_c:
            self._log(f"  Testing concurrency: {current_concurrency}")

            result = await self.run_scenario_at_concurrency(
                scenario=scenario,
                concurrency=current_concurrency,
                num_requests=reqs,
            )
            summary.results.append(result)

            # Log result
            self._log(
                f"    Throughput: {result.throughput_rps:.1f} rps, "
                f"P99: {result.p99_latency_ms:.0f}ms, "
                f"Success: {result.success_rate:.1f}%"
            )

            current_concurrency += step_size

        return summary

    async def run_sustained_test(
        self,
        scenario: BaseScenario,
        concurrency: int,
        duration_seconds: int,
        requests_per_batch: int = 10,
    ) -> ScenarioSummary:
        """Run sustained load test at fixed concurrency."""
        self._log(f"Running sustained test for {scenario.name}")
        self._log(f"  Concurrency: {concurrency}, Duration: {duration_seconds}s")

        summary = ScenarioSummary(
            scenario_id=scenario.id,
            scenario_name=scenario.name,
            category=scenario.category,
            language=scenario.language,
        )

        start_time = time.time()
        batch_num = 0

        while (time.time() - start_time) < duration_seconds:
            batch_num += 1
            elapsed = time.time() - start_time
            self._log(f"  Batch {batch_num} ({elapsed:.0f}s elapsed)")

            result = await self.run_scenario_at_concurrency(
                scenario=scenario,
                concurrency=concurrency,
                num_requests=requests_per_batch,
            )
            summary.results.append(result)

            self._log(
                f"    Throughput: {result.throughput_rps:.1f} rps, "
                f"P99: {result.p99_latency_ms:.0f}ms"
            )

        return summary

    async def run_step_test(
        self,
        scenario: BaseScenario,
        steps: List[tuple],  # [(concurrency, duration_seconds), ...]
    ) -> ScenarioSummary:
        """Run step test with predefined concurrency levels."""
        self._log(f"Running step test for {scenario.name}")

        summary = ScenarioSummary(
            scenario_id=scenario.id,
            scenario_name=scenario.name,
            category=scenario.category,
            language=scenario.language,
        )

        for i, (concurrency, duration) in enumerate(steps):
            self._log(f"  Step {i + 1}/{len(steps)}: concurrency={concurrency}, duration={duration}s")

            step_summary = await self.run_sustained_test(
                scenario=scenario,
                concurrency=concurrency,
                duration_seconds=duration,
            )
            summary.results.extend(step_summary.results)

        return summary

    async def run_scenarios(
        self,
        scenarios: List[BaseScenario],
        test_type: str = "ramp_up",  # "ramp_up", "sustained", "quick"
        **kwargs,
    ) -> List[ScenarioSummary]:
        """Run multiple scenarios."""
        results = []

        for i, scenario in enumerate(scenarios):
            self._log(f"\nScenario {i + 1}/{len(scenarios)}: {scenario.name}")

            if test_type == "ramp_up":
                summary = await self.run_ramp_up_test(scenario, **kwargs)
            elif test_type == "sustained":
                concurrency = kwargs.get("concurrency", 10)
                duration = kwargs.get("duration_seconds", 60)
                summary = await self.run_sustained_test(
                    scenario, concurrency, duration
                )
            elif test_type == "quick":
                # Quick test: just one concurrency level
                summary = ScenarioSummary(
                    scenario_id=scenario.id,
                    scenario_name=scenario.name,
                    category=scenario.category,
                    language=scenario.language,
                )
                result = await self.run_scenario_at_concurrency(
                    scenario=scenario,
                    concurrency=kwargs.get("concurrency", 5),
                    num_requests=kwargs.get("num_requests", 20),
                )
                summary.results.append(result)
            else:
                raise ValueError(f"Unknown test type: {test_type}")

            results.append(summary)

        return results

    async def run_full_suite(
        self,
        scenarios: List[BaseScenario],
    ) -> LoadTestReport:
        """Run complete test suite and generate report."""
        test_id = f"load-test-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"

        self._log(f"Starting full test suite: {test_id}")
        self._log(f"Scenarios: {len(scenarios)}")
        self._log(f"Concurrency range: {self.config.min_concurrency}-{self.config.max_concurrency}")

        start_time = datetime.now(timezone.utc)

        # Warmup
        if self.config.warmup_requests > 0:
            await self.warmup()

        # Create separate monitor for overall metrics to avoid clearing samples
        # when run_scenario_at_concurrency() calls start()/stop() on self.monitor
        overall_monitor = ResourceMonitor(
            sample_interval=self.config.monitor_interval_seconds,
            enable_sandbox_stats=self.config.enable_sandbox_stats,
        )
        await overall_monitor.start()

        # Run all scenarios
        scenario_results = await self.run_scenarios(
            scenarios=scenarios,
            test_type="ramp_up",
        )

        # Stop overall monitoring
        overall_metrics = await overall_monitor.stop()

        end_time = datetime.now(timezone.utc)

        # Create report
        report = LoadTestReport(
            test_id=test_id,
            environment=self.config.environment,
            start_time=start_time,
            end_time=end_time,
            config=self.config,
            scenarios=scenario_results,
            overall_system_metrics=overall_metrics,
        )

        self._log(f"\nTest suite complete: {test_id}")
        self._log(f"Duration: {report.duration_seconds:.0f}s")
        self._log(f"Total requests: {report.total_requests}")
        self._log(f"Success rate: {report.overall_success_rate:.1f}%")

        return report


async def run_quick_test(
    base_url: str,
    api_key: str,
    scenarios: List[BaseScenario],
    concurrency: int = 5,
    requests: int = 20,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> LoadTestReport:
    """Run a quick test with minimal configuration."""
    config = LoadTestConfig(
        base_url=base_url,
        api_key=api_key,
        min_concurrency=concurrency,
        max_concurrency=concurrency,
        concurrency_step=1,
        requests_per_step=requests,
        warmup_requests=5,
    )

    async with LoadTestRunner(config, progress_callback) as runner:
        return await runner.run_full_suite(scenarios)
