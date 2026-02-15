"""Resource monitoring for load testing."""

import asyncio
import subprocess
import json
from typing import Any, Dict, List, Optional

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

from .models import SandboxStats, SystemMetrics


class ResourceMonitor:
    """Monitor system resources during load testing."""

    def __init__(
        self,
        sample_interval: float = 1.0,
        enable_sandbox_stats: bool = True,
    ):
        self.sample_interval = sample_interval
        self.enable_sandbox_stats = enable_sandbox_stats
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._samples: List[SystemMetrics] = []
        self._sandbox_samples: List[SandboxStats] = []
        self._initial_disk_io: Optional[tuple] = None
        self._initial_net_io: Optional[tuple] = None

    async def start(self) -> None:
        """Start monitoring in background."""
        if self._running:
            return

        self._running = True
        self._samples = []
        self._sandbox_samples = []

        # Capture initial I/O counters
        if PSUTIL_AVAILABLE:
            disk_io = psutil.disk_io_counters()
            if disk_io:
                self._initial_disk_io = (disk_io.read_bytes, disk_io.write_bytes)
            net_io = psutil.net_io_counters()
            if net_io:
                self._initial_net_io = (net_io.bytes_sent, net_io.bytes_recv)

        self._task = asyncio.create_task(self._sampling_loop())

    async def stop(self) -> SystemMetrics:
        """Stop monitoring and return aggregated metrics."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        return self._aggregate_metrics()

    async def _sampling_loop(self) -> None:
        """Background loop to sample metrics."""
        while self._running:
            try:
                metrics = self._get_current_system_metrics()
                self._samples.append(metrics)

                if self.enable_sandbox_stats:
                    sandbox_stats = self._get_sandbox_stats()
                    if sandbox_stats:
                        self._sandbox_samples.append(sandbox_stats)

                await asyncio.sleep(self.sample_interval)
            except asyncio.CancelledError:
                break
            except Exception:
                # Continue sampling even if one sample fails
                await asyncio.sleep(self.sample_interval)

    def _get_current_system_metrics(self) -> SystemMetrics:
        """Get current system metrics snapshot."""
        if not PSUTIL_AVAILABLE:
            return SystemMetrics()

        cpu_percent = psutil.cpu_percent(interval=None)
        memory = psutil.virtual_memory()

        # Calculate I/O deltas
        disk_read_mb = 0.0
        disk_write_mb = 0.0
        if self._initial_disk_io:
            disk_io = psutil.disk_io_counters()
            if disk_io:
                disk_read_mb = (disk_io.read_bytes - self._initial_disk_io[0]) / (1024 * 1024)
                disk_write_mb = (disk_io.write_bytes - self._initial_disk_io[1]) / (1024 * 1024)

        net_sent_mb = 0.0
        net_recv_mb = 0.0
        if self._initial_net_io:
            net_io = psutil.net_io_counters()
            if net_io:
                net_sent_mb = (net_io.bytes_sent - self._initial_net_io[0]) / (1024 * 1024)
                net_recv_mb = (net_io.bytes_recv - self._initial_net_io[1]) / (1024 * 1024)

        return SystemMetrics(
            cpu_percent_avg=cpu_percent,
            cpu_percent_max=cpu_percent,
            memory_percent_avg=memory.percent,
            memory_percent_max=memory.percent,
            memory_mb_used=memory.used / (1024 * 1024),
            memory_mb_available=memory.available / (1024 * 1024),
            disk_read_mb=disk_read_mb,
            disk_write_mb=disk_write_mb,
            network_sent_mb=net_sent_mb,
            network_recv_mb=net_recv_mb,
        )

    def _get_sandbox_stats(self) -> Optional[SandboxStats]:
        """Get nsjail sandbox process statistics."""
        if not PSUTIL_AVAILABLE:
            return None

        try:
            nsjail_procs = []
            total_cpu = 0.0
            total_memory_mb = 0.0

            for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
                try:
                    if proc.info["name"] and "nsjail" in proc.info["name"]:
                        cpu = proc.info.get("cpu_percent", 0.0) or 0.0
                        mem_info = proc.info.get("memory_info")
                        mem_mb = mem_info.rss / (1024 * 1024) if mem_info else 0.0

                        total_cpu += cpu
                        total_memory_mb += mem_mb

                        nsjail_procs.append({
                            "name": f"nsjail-{proc.info['pid']}",
                            "cpu_percent": cpu,
                            "memory_mb": mem_mb,
                        })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            return SandboxStats(
                container_count=len(nsjail_procs),
                total_cpu_percent=total_cpu,
                total_memory_mb=total_memory_mb,
                containers=nsjail_procs,
            )

        except Exception:
            return None

    def _aggregate_metrics(self) -> SystemMetrics:
        """Aggregate all samples into summary metrics."""
        if not self._samples:
            return SystemMetrics()

        cpu_values = [s.cpu_percent_avg for s in self._samples]
        memory_values = [s.memory_percent_avg for s in self._samples]
        memory_used = [s.memory_mb_used for s in self._samples]

        # Get final I/O values from last sample
        last_sample = self._samples[-1]

        return SystemMetrics(
            cpu_percent_avg=sum(cpu_values) / len(cpu_values),
            cpu_percent_max=max(cpu_values),
            memory_percent_avg=sum(memory_values) / len(memory_values),
            memory_percent_max=max(memory_values),
            memory_mb_used=sum(memory_used) / len(memory_used),
            memory_mb_available=last_sample.memory_mb_available,
            disk_read_mb=last_sample.disk_read_mb,
            disk_write_mb=last_sample.disk_write_mb,
            network_sent_mb=last_sample.network_sent_mb,
            network_recv_mb=last_sample.network_recv_mb,
        )

    def get_sandbox_summary(self) -> Optional[SandboxStats]:
        """Get aggregated sandbox statistics."""
        if not self._sandbox_samples:
            return None

        # Calculate averages
        container_counts = [s.container_count for s in self._sandbox_samples]
        cpu_totals = [s.total_cpu_percent for s in self._sandbox_samples]
        memory_totals = [s.total_memory_mb for s in self._sandbox_samples]

        return SandboxStats(
            container_count=max(container_counts) if container_counts else 0,
            total_cpu_percent=sum(cpu_totals) / len(cpu_totals) if cpu_totals else 0.0,
            total_memory_mb=max(memory_totals) if memory_totals else 0.0,
            containers=[],  # Don't include individual details in summary
        )

    def get_current_metrics(self) -> SystemMetrics:
        """Get current system metrics snapshot (synchronous)."""
        return self._get_current_system_metrics()


async def get_sandbox_process_count() -> int:
    """Get count of running nsjail sandbox processes."""
    if not PSUTIL_AVAILABLE:
        return 0
    try:
        count = 0
        for proc in psutil.process_iter(["name"]):
            try:
                if proc.info["name"] and "nsjail" in proc.info["name"]:
                    count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return count
    except Exception:
        return 0
