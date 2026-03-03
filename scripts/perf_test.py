#!/usr/bin/env python3
"""Performance testing script for Code Interpreter API.

Compares latency, throughput, and resource usage between versions.
Includes complexity-based baseline testing for REPL optimization.
"""

import asyncio
import aiohttp
import time
import statistics
import json
import sys
import os
from dataclasses import dataclass, field
from typing import List, Dict, Any, OrderedDict
from datetime import datetime
from collections import OrderedDict as OD


# Code samples for complexity-based baseline testing
# Each level measures different aspects of Python execution overhead
CODE_SAMPLES: Dict[str, Dict[str, Any]] = OD([
    ("1_minimal", {
        "name": "Minimal (no imports)",
        "description": "Pure interpreter startup, no imports",
        "code": "print('hello')",
    }),
    ("2_stdlib", {
        "name": "Stdlib Only",
        "description": "Standard library imports only",
        "code": "import json, os, sys, re, math; print(json.dumps({'ok': True, 'pid': os.getpid()}))",
    }),
    ("3_numpy", {
        "name": "NumPy (pre-loaded)",
        "description": "Single heavy pre-installed package",
        "code": "import numpy as np; print(f'mean={np.mean([1,2,3,4,5])}')",
    }),
    ("4_pandas", {
        "name": "Pandas (pre-loaded)",
        "description": "Pandas with DataFrame operations",
        "code": """import pandas as pd
import numpy as np
df = pd.DataFrame({'a': np.random.rand(100), 'b': np.random.rand(100)})
print(f'shape={df.shape}, mean_a={df["a"].mean():.4f}')""",
    }),
    ("5_matplotlib", {
        "name": "Matplotlib (file generation)",
        "description": "Plot generation with file I/O",
        "code": """import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
x = np.linspace(0, 10, 100)
plt.figure(figsize=(8, 6))
plt.plot(x, np.sin(x), label='sin(x)')
plt.plot(x, np.cos(x), label='cos(x)')
plt.legend()
plt.savefig('/mnt/data/plot.png', dpi=100)
plt.close()
print('saved plot.png')""",
    }),
    ("6_sklearn", {
        "name": "Scikit-learn (ML)",
        "description": "Machine learning model training",
        "code": """import numpy as np
from sklearn.linear_model import LinearRegression
from sklearn.datasets import make_regression
X, y = make_regression(n_samples=100, n_features=5, noise=0.1)
model = LinearRegression()
model.fit(X, y)
print(f'score={model.score(X, y):.4f}')""",
    }),
    ("7_multi_import", {
        "name": "Multi-import (heavy)",
        "description": "Multiple heavy packages imported together",
        "code": """import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy import stats
df = pd.DataFrame({'x': np.random.rand(50), 'y': np.random.rand(50)})
corr, pval = stats.pearsonr(df['x'], df['y'])
print(f'correlation={corr:.4f}, p={pval:.4f}')""",
    }),
    ("8_sympy", {
        "name": "SymPy (symbolic math)",
        "description": "Symbolic computation package",
        "code": """import sympy as sp
x = sp.Symbol('x')
expr = sp.sin(x)**2 + sp.cos(x)**2
simplified = sp.simplify(expr)
print(f'simplified: {simplified}')""",
    }),
    ("9_opencv", {
        "name": "OpenCV (image processing)",
        "description": "Computer vision package",
        "code": """import cv2
import numpy as np
img = np.zeros((100, 100, 3), dtype=np.uint8)
cv2.circle(img, (50, 50), 30, (0, 255, 0), -1)
cv2.imwrite('/mnt/data/circle.png', img)
print(f'created image: shape={img.shape}')""",
    }),
    ("10_complex_computation", {
        "name": "Complex Computation",
        "description": "Heavy computation with multiple packages",
        "code": """import numpy as np
import pandas as pd
from scipy import optimize

def objective(x):
    return (x[0] - 1)**2 + (x[1] - 2.5)**2

result = optimize.minimize(objective, [0, 0], method='BFGS')
df = pd.DataFrame({'param': ['x', 'y'], 'value': result.x})
print(f'optimization result: x={result.x[0]:.4f}, y={result.x[1]:.4f}')""",
    }),
])


@dataclass
class TestResult:
    """Single test execution result."""
    success: bool
    latency_ms: float
    status_code: int
    error: str = ""


@dataclass
class TestSummary:
    """Summary of test results."""
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    latencies: List[float] = field(default_factory=list)
    errors: Dict[str, int] = field(default_factory=dict)
    start_time: float = 0
    end_time: float = 0

    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 0
        return (self.successful_requests / self.total_requests) * 100

    @property
    def avg_latency_ms(self) -> float:
        if not self.latencies:
            return 0
        return statistics.mean(self.latencies)

    @property
    def p50_latency_ms(self) -> float:
        if not self.latencies:
            return 0
        return statistics.median(self.latencies)

    @property
    def p95_latency_ms(self) -> float:
        if not self.latencies:
            return 0
        sorted_latencies = sorted(self.latencies)
        idx = int(len(sorted_latencies) * 0.95)
        return sorted_latencies[min(idx, len(sorted_latencies) - 1)]

    @property
    def p99_latency_ms(self) -> float:
        if not self.latencies:
            return 0
        sorted_latencies = sorted(self.latencies)
        idx = int(len(sorted_latencies) * 0.99)
        return sorted_latencies[min(idx, len(sorted_latencies) - 1)]

    @property
    def throughput_rps(self) -> float:
        duration = self.end_time - self.start_time
        if duration == 0:
            return 0
        return self.successful_requests / duration


class PerformanceTester:
    """Performance tester for the Code Interpreter API."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key
        }

    async def execute_single(self, session: aiohttp.ClientSession, code: str, lang: str = "py") -> TestResult:
        """Execute a single code request."""
        start = time.perf_counter()
        try:
            payload = {
                "lang": lang,
                "code": code,
                "entity_id": f"perf-test-{int(time.time())}",
                "user_id": "perf-tester"
            }

            async with session.post(
                f"{self.base_url}/exec",
                json=payload,
                headers=self.headers,
                ssl=False,
                timeout=aiohttp.ClientTimeout(total=60)
            ) as response:
                latency = (time.perf_counter() - start) * 1000
                await response.read()
                return TestResult(
                    success=response.status == 200,
                    latency_ms=latency,
                    status_code=response.status,
                    error="" if response.status == 200 else f"HTTP {response.status}"
                )
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return TestResult(
                success=False,
                latency_ms=latency,
                status_code=0,
                error=str(e)
            )

    async def run_sequential_test(self, num_requests: int, code: str, lang: str = "py") -> TestSummary:
        """Run sequential requests (measures single-request latency)."""
        summary = TestSummary()
        summary.start_time = time.perf_counter()

        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            for i in range(num_requests):
                result = await self.execute_single(session, code, lang)
                summary.total_requests += 1

                if result.success:
                    summary.successful_requests += 1
                    summary.latencies.append(result.latency_ms)
                else:
                    summary.failed_requests += 1
                    error_key = result.error[:50]
                    summary.errors[error_key] = summary.errors.get(error_key, 0) + 1

                # Progress indicator
                if (i + 1) % 5 == 0:
                    print(f"  Progress: {i + 1}/{num_requests}", end='\r')

        summary.end_time = time.perf_counter()
        print()  # Clear progress line
        return summary

    async def run_concurrent_test(self, num_requests: int, concurrency: int, code: str, lang: str = "py") -> TestSummary:
        """Run concurrent requests (measures throughput under load)."""
        summary = TestSummary()
        summary.start_time = time.perf_counter()

        semaphore = asyncio.Semaphore(concurrency)

        async def bounded_request(session: aiohttp.ClientSession) -> TestResult:
            async with semaphore:
                return await self.execute_single(session, code, lang)

        connector = aiohttp.TCPConnector(ssl=False, limit=concurrency * 2)
        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [bounded_request(session) for _ in range(num_requests)]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            summary.total_requests += 1
            if isinstance(result, Exception):
                summary.failed_requests += 1
                summary.errors[str(result)[:50]] = summary.errors.get(str(result)[:50], 0) + 1
            elif result.success:
                summary.successful_requests += 1
                summary.latencies.append(result.latency_ms)
            else:
                summary.failed_requests += 1
                summary.errors[result.error[:50]] = summary.errors.get(result.error[:50], 0) + 1

        summary.end_time = time.perf_counter()
        return summary


def print_summary(name: str, summary: TestSummary):
    """Print test summary."""
    print(f"\n{'=' * 60}")
    print(f"  {name}")
    print(f"{'=' * 60}")
    print(f"  Total Requests:     {summary.total_requests}")
    print(f"  Successful:         {summary.successful_requests}")
    print(f"  Failed:             {summary.failed_requests}")
    print(f"  Success Rate:       {summary.success_rate:.1f}%")
    print(f"  Duration:           {summary.end_time - summary.start_time:.2f}s")
    print(f"  Throughput:         {summary.throughput_rps:.2f} req/s")
    print()
    print(f"  Latency (ms):")
    print(f"    Average:          {summary.avg_latency_ms:.1f}")
    print(f"    P50 (median):     {summary.p50_latency_ms:.1f}")
    print(f"    P95:              {summary.p95_latency_ms:.1f}")
    print(f"    P99:              {summary.p99_latency_ms:.1f}")
    if summary.latencies:
        print(f"    Min:              {min(summary.latencies):.1f}")
        print(f"    Max:              {max(summary.latencies):.1f}")

    if summary.errors:
        print(f"\n  Errors:")
        for error, count in summary.errors.items():
            print(f"    {error}: {count}")


def compare_results(old: TestSummary, new: TestSummary):
    """Compare old vs new results."""
    print(f"\n{'=' * 60}")
    print(f"  COMPARISON: Old (PRO) vs New (Refactored)")
    print(f"{'=' * 60}")

    def delta(old_val, new_val, lower_is_better=True):
        if old_val == 0:
            return "N/A"
        pct = ((new_val - old_val) / old_val) * 100
        direction = "faster" if (pct < 0 and lower_is_better) or (pct > 0 and not lower_is_better) else "slower"
        return f"{abs(pct):.1f}% {direction}"

    print(f"\n  Throughput:")
    print(f"    Old:    {old.throughput_rps:.2f} req/s")
    print(f"    New:    {new.throughput_rps:.2f} req/s")
    print(f"    Delta:  {delta(old.throughput_rps, new.throughput_rps, lower_is_better=False)}")

    print(f"\n  Average Latency:")
    print(f"    Old:    {old.avg_latency_ms:.1f} ms")
    print(f"    New:    {new.avg_latency_ms:.1f} ms")
    print(f"    Delta:  {delta(old.avg_latency_ms, new.avg_latency_ms)}")

    print(f"\n  P95 Latency:")
    print(f"    Old:    {old.p95_latency_ms:.1f} ms")
    print(f"    New:    {new.p95_latency_ms:.1f} ms")
    print(f"    Delta:  {delta(old.p95_latency_ms, new.p95_latency_ms)}")

    print(f"\n  Success Rate:")
    print(f"    Old:    {old.success_rate:.1f}%")
    print(f"    New:    {new.success_rate:.1f}%")


async def run_test_suite(base_url: str, api_key: str, test_name: str) -> Dict[str, TestSummary]:
    """Run the full test suite."""
    tester = PerformanceTester(base_url, api_key)
    results = {}

    # Simple Python code for testing
    simple_code = "print('Hello, World!')"
    compute_code = "sum([i**2 for i in range(1000)])"

    print(f"\n{'#' * 60}")
    print(f"  Testing: {test_name}")
    print(f"  URL: {base_url}")
    print(f"{'#' * 60}")

    # Test 1: Sequential simple requests (baseline latency)
    print("\n[Test 1] Sequential Simple Requests (10 requests)")
    results['sequential_simple'] = await tester.run_sequential_test(10, simple_code)
    print_summary("Sequential Simple", results['sequential_simple'])

    # Test 2: Sequential compute requests
    print("\n[Test 2] Sequential Compute Requests (10 requests)")
    results['sequential_compute'] = await tester.run_sequential_test(10, compute_code)
    print_summary("Sequential Compute", results['sequential_compute'])

    # Test 3: Concurrent requests (low concurrency)
    print("\n[Test 3] Concurrent Requests (20 requests, 5 concurrent)")
    results['concurrent_low'] = await tester.run_concurrent_test(20, 5, simple_code)
    print_summary("Concurrent (5)", results['concurrent_low'])

    # Test 4: Concurrent requests (higher concurrency)
    print("\n[Test 4] Concurrent Requests (30 requests, 10 concurrent)")
    results['concurrent_high'] = await tester.run_concurrent_test(30, 10, simple_code)
    print_summary("Concurrent (10)", results['concurrent_high'])

    return results


async def run_complexity_baseline(base_url: str, api_key: str, num_requests: int = 5) -> Dict[str, Dict[str, Any]]:
    """Run complexity-based baseline tests.

    Tests different code complexity levels to measure:
    - Pure interpreter startup time
    - Import overhead for different packages
    - File generation overhead
    - Cumulative import costs

    Returns dict with results per complexity level.
    """
    tester = PerformanceTester(base_url, api_key)
    results = {}

    print(f"\n{'#' * 70}")
    print(f"  COMPLEXITY-BASED BASELINE TESTING")
    print(f"  Running {num_requests} sequential requests per complexity level")
    print(f"{'#' * 70}")

    for level_key, level_info in CODE_SAMPLES.items():
        name = level_info["name"]
        description = level_info["description"]
        code = level_info["code"]

        print(f"\n[{level_key}] {name}")
        print(f"  Description: {description}")
        print(f"  Code preview: {code[:60]}..." if len(code) > 60 else f"  Code: {code}")

        summary = await tester.run_sequential_test(num_requests, code)

        results[level_key] = {
            "name": name,
            "description": description,
            "total_requests": summary.total_requests,
            "successful_requests": summary.successful_requests,
            "failed_requests": summary.failed_requests,
            "success_rate": summary.success_rate,
            "avg_latency_ms": summary.avg_latency_ms,
            "p50_latency_ms": summary.p50_latency_ms,
            "p95_latency_ms": summary.p95_latency_ms,
            "min_latency_ms": min(summary.latencies) if summary.latencies else 0,
            "max_latency_ms": max(summary.latencies) if summary.latencies else 0,
            "latencies": summary.latencies,
            "errors": summary.errors,
        }

        # Print summary inline
        if summary.latencies:
            print(f"  Results: avg={summary.avg_latency_ms:.0f}ms, "
                  f"min={min(summary.latencies):.0f}ms, "
                  f"max={max(summary.latencies):.0f}ms, "
                  f"success={summary.success_rate:.0f}%")
        else:
            print(f"  Results: FAILED - {summary.errors}")

    return results


def print_complexity_summary(results: Dict[str, Dict[str, Any]]):
    """Print a summary table of complexity baseline results."""
    print(f"\n{'=' * 80}")
    print(f"  COMPLEXITY BASELINE SUMMARY")
    print(f"{'=' * 80}")
    print(f"\n  {'Level':<30} {'Avg (ms)':<12} {'Min (ms)':<12} {'Max (ms)':<12} {'Success':<10}")
    print(f"  {'-' * 74}")

    baseline_avg = None
    for level_key, data in results.items():
        name = data["name"][:28]
        avg = data["avg_latency_ms"]
        min_lat = data["min_latency_ms"]
        max_lat = data["max_latency_ms"]
        success = data["success_rate"]

        # Track baseline for delta calculation
        if baseline_avg is None:
            baseline_avg = avg
            delta = ""
        else:
            delta_ms = avg - baseline_avg
            delta = f" (+{delta_ms:.0f}ms)" if delta_ms > 0 else f" ({delta_ms:.0f}ms)"

        print(f"  {name:<30} {avg:<12.0f} {min_lat:<12.0f} {max_lat:<12.0f} {success:<10.0f}%{delta}")

    print(f"\n  Key insights:")
    if results:
        minimal = results.get("1_minimal", {}).get("avg_latency_ms", 0)
        numpy_lat = results.get("3_numpy", {}).get("avg_latency_ms", 0)
        matplotlib_lat = results.get("5_matplotlib", {}).get("avg_latency_ms", 0)
        multi_lat = results.get("7_multi_import", {}).get("avg_latency_ms", 0)

        if minimal > 0:
            print(f"  - Pure interpreter startup: ~{minimal:.0f}ms")
        if numpy_lat > 0 and minimal > 0:
            print(f"  - NumPy import overhead: ~{numpy_lat - minimal:.0f}ms")
        if matplotlib_lat > 0 and minimal > 0:
            print(f"  - Matplotlib + file I/O overhead: ~{matplotlib_lat - minimal:.0f}ms")
        if multi_lat > 0 and minimal > 0:
            print(f"  - Multi-import overhead: ~{multi_lat - minimal:.0f}ms")


def save_baseline_results(results: Dict[str, Dict[str, Any]], filename: str = "baseline_complexity.json"):
    """Save baseline results to JSON file."""
    output = {
        "timestamp": datetime.now().isoformat(),
        "description": "Complexity-based baseline testing for REPL optimization",
        "results": results,
    }

    # Remove raw latencies list for cleaner output
    for level in output["results"].values():
        if "latencies" in level:
            del level["latencies"]

    filepath = os.path.join(os.path.dirname(__file__), "..", filename)
    with open(filepath, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"\n  Results saved to: {filepath}")
    return filepath


async def main():
    api_key = "test-api-key-for-development-only"
    base_url = "https://localhost"

    print("\n" + "=" * 60)
    print("  CODE INTERPRETER API - PERFORMANCE COMPARISON")
    print("  Old Version (PRO) vs New Version (Refactored)")
    print("=" * 60)

    # Test the new version (currently running)
    print("\n\n>>> TESTING NEW VERSION (Refactored)")
    new_results = await run_test_suite(base_url, api_key, "New (Refactored)")

    # Store new results
    with open('/tmp/new_results.json', 'w') as f:
        json.dump({
            k: {
                'throughput': v.throughput_rps,
                'avg_latency': v.avg_latency_ms,
                'p95_latency': v.p95_latency_ms,
                'success_rate': v.success_rate
            }
            for k, v in new_results.items()
        }, f)

    print("\n\n>>> New version testing complete.")
    print(">>> Results saved to /tmp/new_results.json")
    print("\n>>> To test the old version:")
    print(">>>   1. Stop the running API server")
    print(">>>   2. Switch to the old version branch")
    print(">>>   3. Start the API server again")
    print(">>>   4. Run this script again with --old flag")


async def main_baseline(num_requests: int = 5):
    """Run complexity baseline testing."""
    api_key = os.environ.get("API_KEY", "test-api-key-for-development-only")
    base_url = os.environ.get("BASE_URL", "https://localhost")

    print("\n" + "=" * 70)
    print("  CODE INTERPRETER API - COMPLEXITY BASELINE TESTING")
    print("  Measuring execution latency across different code complexity levels")
    print("=" * 70)
    print(f"\n  API URL: {base_url}")
    print(f"  Requests per level: {num_requests}")

    # Run complexity baseline tests
    results = await run_complexity_baseline(base_url, api_key, num_requests)

    # Print summary
    print_complexity_summary(results)

    # Save results
    save_baseline_results(results)

    print("\n" + "=" * 70)
    print("  BASELINE TESTING COMPLETE")
    print("=" * 70)
    print("\n  Use these results to compare after REPL optimization:")
    print("  - Pure interpreter startup time (minimal test)")
    print("  - Package import overhead (numpy, pandas, matplotlib)")
    print("  - File generation overhead (matplotlib, opencv)")
    print("  - Multi-import cumulative overhead")


def print_usage():
    """Print usage information."""
    print("""
Usage: python perf_test.py [OPTIONS]

Options:
  --baseline       Run complexity-based baseline testing (recommended before REPL optimization)
  --baseline=N     Run baseline with N requests per level (default: 5)
  --old            Compare with old version results
  (no args)        Run standard performance comparison tests

Environment Variables:
  API_KEY          API key for authentication (default: test-api-key-for-development-only)
  BASE_URL         Base URL for API (default: https://localhost)

Examples:
  python perf_test.py --baseline          # Run baseline with 5 requests per level
  python perf_test.py --baseline=10       # Run baseline with 10 requests per level
  python perf_test.py                     # Run standard performance tests
""")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        print_usage()
    elif any(arg.startswith("--baseline") for arg in sys.argv):
        # Extract number of requests if specified
        num_requests = 5
        for arg in sys.argv:
            if arg.startswith("--baseline="):
                try:
                    num_requests = int(arg.split("=")[1])
                except ValueError:
                    print(f"Invalid number of requests: {arg}")
                    sys.exit(1)
        asyncio.run(main_baseline(num_requests))
    elif "--old" in sys.argv:
        # Load new results and compare
        print("Old version testing mode - implement as needed")
    else:
        asyncio.run(main())
