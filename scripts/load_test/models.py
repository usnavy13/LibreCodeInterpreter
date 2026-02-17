"""Data models for load testing results and metrics."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import statistics


@dataclass
class ExecutionResult:
    """Single execution result."""

    success: bool
    latency_ms: float
    status_code: int
    language: str
    scenario_id: str
    error: Optional[str] = None
    memory_mb: Optional[float] = None
    files_generated: int = 0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    container_source: Optional[str] = None  # "pool" or "cold"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "latency_ms": self.latency_ms,
            "status_code": self.status_code,
            "language": self.language,
            "scenario_id": self.scenario_id,
            "error": self.error,
            "memory_mb": self.memory_mb,
            "files_generated": self.files_generated,
            "timestamp": self.timestamp.isoformat(),
            "container_source": self.container_source,
        }


@dataclass
class SystemMetrics:
    """System resource metrics during test."""

    cpu_percent_avg: float = 0.0
    cpu_percent_max: float = 0.0
    memory_percent_avg: float = 0.0
    memory_percent_max: float = 0.0
    memory_mb_used: float = 0.0
    memory_mb_available: float = 0.0
    disk_read_mb: float = 0.0
    disk_write_mb: float = 0.0
    network_sent_mb: float = 0.0
    network_recv_mb: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cpu_percent_avg": round(self.cpu_percent_avg, 2),
            "cpu_percent_max": round(self.cpu_percent_max, 2),
            "memory_percent_avg": round(self.memory_percent_avg, 2),
            "memory_percent_max": round(self.memory_percent_max, 2),
            "memory_mb_used": round(self.memory_mb_used, 2),
            "memory_mb_available": round(self.memory_mb_available, 2),
            "disk_read_mb": round(self.disk_read_mb, 2),
            "disk_write_mb": round(self.disk_write_mb, 2),
            "network_sent_mb": round(self.network_sent_mb, 2),
            "network_recv_mb": round(self.network_recv_mb, 2),
        }


@dataclass
class SandboxStats:
    """Sandbox process resource statistics."""

    container_count: int = 0
    total_cpu_percent: float = 0.0
    total_memory_mb: float = 0.0
    containers: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "container_count": self.container_count,
            "total_cpu_percent": round(self.total_cpu_percent, 2),
            "total_memory_mb": round(self.total_memory_mb, 2),
            "containers": self.containers,
        }


@dataclass
class ConcurrencyTestResult:
    """Results for a specific concurrency level."""

    concurrency: int
    scenario_id: str
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    latencies: List[float] = field(default_factory=list)
    errors: Dict[str, int] = field(default_factory=dict)
    duration_seconds: float = 0.0
    system_metrics: SystemMetrics = field(default_factory=SystemMetrics)
    sandbox_stats: Optional[SandboxStats] = None

    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return (self.successful_requests / self.total_requests) * 100

    @property
    def error_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return (self.failed_requests / self.total_requests) * 100

    @property
    def throughput_rps(self) -> float:
        if self.duration_seconds == 0:
            return 0.0
        return self.successful_requests / self.duration_seconds

    @property
    def avg_latency_ms(self) -> float:
        if not self.latencies:
            return 0.0
        return statistics.mean(self.latencies)

    @property
    def p50_latency_ms(self) -> float:
        if not self.latencies:
            return 0.0
        return statistics.median(self.latencies)

    @property
    def p95_latency_ms(self) -> float:
        if not self.latencies:
            return 0.0
        sorted_latencies = sorted(self.latencies)
        idx = int(len(sorted_latencies) * 0.95)
        return sorted_latencies[min(idx, len(sorted_latencies) - 1)]

    @property
    def p99_latency_ms(self) -> float:
        if not self.latencies:
            return 0.0
        sorted_latencies = sorted(self.latencies)
        idx = int(len(sorted_latencies) * 0.99)
        return sorted_latencies[min(idx, len(sorted_latencies) - 1)]

    @property
    def min_latency_ms(self) -> float:
        if not self.latencies:
            return 0.0
        return min(self.latencies)

    @property
    def max_latency_ms(self) -> float:
        if not self.latencies:
            return 0.0
        return max(self.latencies)

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "concurrency": self.concurrency,
            "scenario_id": self.scenario_id,
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "success_rate": round(self.success_rate, 2),
            "error_rate": round(self.error_rate, 2),
            "throughput_rps": round(self.throughput_rps, 2),
            "duration_seconds": round(self.duration_seconds, 2),
            "latency": {
                "avg_ms": round(self.avg_latency_ms, 2),
                "p50_ms": round(self.p50_latency_ms, 2),
                "p95_ms": round(self.p95_latency_ms, 2),
                "p99_ms": round(self.p99_latency_ms, 2),
                "min_ms": round(self.min_latency_ms, 2),
                "max_ms": round(self.max_latency_ms, 2),
            },
            "errors": self.errors,
            "system_metrics": self.system_metrics.to_dict(),
        }
        if self.sandbox_stats:
            result["sandbox_stats"] = self.sandbox_stats.to_dict()
        return result


@dataclass
class BreakingPoint:
    """Information about a performance breaking point."""

    concurrency: int
    reason: str  # "latency", "error_rate", "throughput", "timeout"
    details: str
    threshold_value: float
    actual_value: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "concurrency": self.concurrency,
            "reason": self.reason,
            "details": self.details,
            "threshold_value": round(self.threshold_value, 2),
            "actual_value": round(self.actual_value, 2),
        }


@dataclass
class VMRecommendations:
    """VM sizing recommendations based on test results."""

    cpu_cores: int = 0
    memory_gb: int = 0
    azure_vm_type: str = ""
    aws_instance_type: str = ""
    gcp_machine_type: str = ""
    max_safe_concurrency: int = 0
    breaking_point_concurrency: int = 0
    bottleneck: str = ""  # "cpu", "memory", "io", "network"
    confidence_score: float = 0.0
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cpu_cores": self.cpu_cores,
            "memory_gb": self.memory_gb,
            "azure": self.azure_vm_type,
            "aws": self.aws_instance_type,
            "gcp": self.gcp_machine_type,
            "max_safe_concurrency": self.max_safe_concurrency,
            "breaking_point_concurrency": self.breaking_point_concurrency,
            "bottleneck": self.bottleneck,
            "confidence_score": round(self.confidence_score, 2),
            "notes": self.notes,
        }


@dataclass
class ScenarioSummary:
    """Summary of results for a single scenario."""

    scenario_id: str
    scenario_name: str
    category: str
    language: str
    results: List[ConcurrencyTestResult] = field(default_factory=list)
    breaking_point: Optional[BreakingPoint] = None

    @property
    def max_throughput_rps(self) -> float:
        if not self.results:
            return 0.0
        return max(r.throughput_rps for r in self.results)

    @property
    def best_concurrency(self) -> int:
        """Concurrency level with highest throughput."""
        if not self.results:
            return 0
        best = max(self.results, key=lambda r: r.throughput_rps)
        return best.concurrency

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "scenario_id": self.scenario_id,
            "scenario_name": self.scenario_name,
            "category": self.category,
            "language": self.language,
            "max_throughput_rps": round(self.max_throughput_rps, 2),
            "best_concurrency": self.best_concurrency,
            "results": [r.to_dict() for r in self.results],
        }
        if self.breaking_point:
            result["breaking_point"] = self.breaking_point.to_dict()
        return result


@dataclass
class LoadTestConfig:
    """Configuration for load test run."""

    base_url: str
    api_key: str
    environment: str = "unknown"
    min_concurrency: int = 1
    max_concurrency: int = 50
    concurrency_step: int = 5
    requests_per_step: int = 100
    warmup_requests: int = 10
    timeout_seconds: int = 60
    monitor_interval_seconds: float = 1.0
    enable_sandbox_stats: bool = True
    output_dir: str = "./load_test_results"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "base_url": self.base_url,
            "environment": self.environment,
            "min_concurrency": self.min_concurrency,
            "max_concurrency": self.max_concurrency,
            "concurrency_step": self.concurrency_step,
            "requests_per_step": self.requests_per_step,
            "warmup_requests": self.warmup_requests,
            "timeout_seconds": self.timeout_seconds,
            "monitor_interval_seconds": self.monitor_interval_seconds,
            "enable_sandbox_stats": self.enable_sandbox_stats,
            "output_dir": self.output_dir,
        }


@dataclass
class LoadTestReport:
    """Complete load test report."""

    test_id: str
    environment: str
    start_time: datetime
    end_time: datetime
    config: LoadTestConfig
    scenarios: List[ScenarioSummary] = field(default_factory=list)
    overall_system_metrics: SystemMetrics = field(default_factory=SystemMetrics)
    vm_recommendations: VMRecommendations = field(default_factory=VMRecommendations)

    @property
    def duration_seconds(self) -> float:
        return (self.end_time - self.start_time).total_seconds()

    @property
    def total_requests(self) -> int:
        total = 0
        for scenario in self.scenarios:
            for result in scenario.results:
                total += result.total_requests
        return total

    @property
    def total_successful(self) -> int:
        total = 0
        for scenario in self.scenarios:
            for result in scenario.results:
                total += result.successful_requests
        return total

    @property
    def overall_success_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return (self.total_successful / self.total_requests) * 100

    def to_dict(self) -> Dict[str, Any]:
        return {
            "test_id": self.test_id,
            "environment": self.environment,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "duration_seconds": round(self.duration_seconds, 2),
            "summary": {
                "total_requests": self.total_requests,
                "total_successful": self.total_successful,
                "success_rate": round(self.overall_success_rate, 2),
                "scenarios_tested": len(self.scenarios),
            },
            "config": self.config.to_dict(),
            "scenarios": [s.to_dict() for s in self.scenarios],
            "system_metrics": self.overall_system_metrics.to_dict(),
            "vm_recommendations": self.vm_recommendations.to_dict(),
        }
