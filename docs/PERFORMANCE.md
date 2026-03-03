# Performance Guide

This document provides performance benchmarks, tuning recommendations, and monitoring guidance for the Code Interpreter API.

## Performance Benchmarks

### Baseline Metrics (With Optimizations)

The following metrics represent typical performance with all optimizations enabled (sandbox pooling, REPL mode):

| Metric                         | Value      | Notes                           |
| ------------------------------ | ---------- | ------------------------------- |
| **Python execution (simple)**  | 20-40ms    | With REPL mode                  |
| **Python execution (complex)** | 50-200ms   | Depends on code complexity      |
| **JavaScript execution**       | 50-100ms   | One-shot nsjail execution       |
| **Sandbox acquisition**        | ~3ms       | From pre-warmed pool            |
| **Cold start (no pool)**       | 500-2000ms | First request or pool exhausted |
| **State serialization**        | 1-25ms     | Depends on state size           |
| **File upload (1MB)**          | 50-100ms   | To MinIO                        |

### Performance Comparison

| Configuration         | Python Simple | Notes                    |
| --------------------- | ------------- | ------------------------ |
| REPL + Pool (default) | 20-40ms       | 100x faster              |
| Pool only (no REPL)   | 200-500ms     | Pool warmup only         |
| No optimizations      | 3,000-4,000ms | Cold start every request |

---

## Optimization Features

### 1. Sandbox Pool

Pre-warmed Python REPL sandboxes eliminate cold start latency:

```
Without Pool:
Request → Create Sandbox → Start nsjail → Execute → Destroy
         [~500-2000ms]     [~100ms]       [~50ms]   [~50ms]
         Total: ~700-2200ms

With Pool:
Request → Acquire from Pool → Execute → Destroy → (Background: Replenish)
         [~3ms]              [~50ms]   [~50ms]
         Total: ~100ms
```

**Configuration:**

```bash
CONTAINER_POOL_ENABLED=true
CONTAINER_POOL_PY=5                 # Number of pre-warmed Python REPLs
```

**Note:** Only Python supports REPL pooling. All other languages use one-shot nsjail execution.

### 2. REPL Mode (Python)

Pre-warmed Python interpreter with common libraries:

```
Without REPL:
Request → Python startup → Import libs → Execute → Output
         [~2000ms]        [~1000ms]      [~50ms]   [~10ms]
         Total: ~3060ms

With REPL:
Request → Send to REPL → Execute → Output
         [~5ms]         [~25ms]   [~5ms]
         Total: ~35ms
```

**Pre-imported libraries:**

- numpy, pandas, matplotlib, scipy
- sklearn, statsmodels
- json, csv, datetime, collections

**Configuration:**

```bash
REPL_ENABLED=true
REPL_WARMUP_TIMEOUT_SECONDS=15
REPL_HEALTH_CHECK_TIMEOUT_SECONDS=5
```

### 3. Connection Pooling

Redis connections are pooled for efficiency:

```bash
REDIS_MAX_CONNECTIONS=20
REDIS_SOCKET_TIMEOUT=5
REDIS_SOCKET_CONNECT_TIMEOUT=5
```

---

## Configuration for Performance

### Pool Size Recommendations

| Usage Pattern          | Python Pool Size |
| ---------------------- | ---------------- |
| Light (< 10 req/min)   | 2-5              |
| Medium (10-50 req/min) | 5-15             |
| Heavy (> 50 req/min)   | 10-30            |

**Trade-offs:**

- Higher pool size = more memory usage, faster warm responses
- Non-Python languages use one-shot nsjail execution (no pooling)

### Memory Allocation

Each sandbox uses memory:

| Language          | Base Memory | With Code | Recommendation |
| ----------------- | ----------- | --------- | -------------- |
| Python (REPL)     | ~150MB      | 200-500MB | 512MB limit    |
| Python (standard) | ~50MB       | 100-300MB | 512MB limit    |
| JavaScript        | ~50MB       | 100-200MB | 256MB limit    |
| Go                | ~20MB       | 50-150MB  | 256MB limit    |
| Java              | ~100MB      | 200-400MB | 512MB limit    |

**Configuration:**

```bash
MAX_MEMORY_MB=512  # Default per sandbox
```

### State Persistence Tuning

For optimal state persistence performance:

```bash
# Faster state operations (smaller states)
STATE_MAX_SIZE_MB=10

# Less frequent archival (reduces MinIO operations)
STATE_ARCHIVE_CHECK_INTERVAL_SECONDS=600

# Longer Redis TTL (fewer archive restorations)
STATE_TTL_SECONDS=14400  # 4 hours
```

---

## Latency Breakdown

### Typical Python Request (REPL mode)

```
Component                   Time
──────────────────────────────────
Request parsing             ~1ms
Authentication              ~1ms
Session lookup              ~2ms
State load (if exists)      ~3ms
Sandbox acquire             ~3ms
REPL communication          ~5ms
Code execution              ~20ms
State save                  ~3ms
Response building           ~2ms
──────────────────────────────────
Total                       ~40ms
```

### Request with File Operations

```
Component                   Time
──────────────────────────────────
Request parsing             ~1ms
Authentication              ~1ms
Session lookup              ~2ms
File upload to sandbox      ~10ms (1MB file)
Sandbox acquire             ~3ms
Code execution              ~50ms
Output file detection       ~5ms
File download from sandbox  ~10ms
MinIO upload                ~20ms
Response building           ~2ms
──────────────────────────────────
Total                       ~104ms
```

---

## Scaling Guidelines

### Concurrent Requests

The API handles concurrent requests efficiently:

| Concurrency | Response Time (p50) | Response Time (p99) |
| ----------- | ------------------- | ------------------- |
| 1           | 35ms                | 50ms                |
| 5           | 40ms                | 80ms                |
| 10          | 50ms                | 150ms               |
| 20          | 100ms               | 300ms               |
| 50          | 200ms               | 500ms               |

**Bottlenecks at high concurrency:**

1. Sandbox pool exhaustion (wait for replenishment)
2. Redis connection pool saturation
3. nsjail process throughput

### Horizontal Scaling

For high-throughput deployments:

1. **Multiple API instances**: Load balance across instances
2. **Shared Redis**: All instances use same Redis for sessions/state
3. **Shared MinIO**: All instances use same MinIO for files
4. **Separate hosts**: Distribute sandbox load across API instances

```
                    ┌─────────────────┐
                    │  Load Balancer  │
                    └────────┬────────┘
             ┌───────────────┼───────────────┐
             ▼               ▼               ▼
      ┌──────────┐    ┌──────────┐    ┌──────────┐
      │  API 1   │    │  API 2   │    │  API 3   │
      │+ nsjail  │    │+ nsjail  │    │+ nsjail  │
      └────┬─────┘    └────┬─────┘    └────┬─────┘
           │               │               │
           └───────────────┼───────────────┘
                    ┌──────┴──────┐
                    │   Redis     │
                    │   MinIO     │
                    └─────────────┘
```

### Resource Planning

| Daily Requests | Instances | Pool Size (per) | Redis Memory | MinIO Storage |
| -------------- | --------- | --------------- | ------------ | ------------- |
| 1,000          | 1         | 5 Python        | 256MB        | 1GB           |
| 10,000         | 2         | 10 Python       | 512MB        | 5GB           |
| 100,000        | 5         | 15 Python       | 2GB          | 20GB          |
| 1,000,000      | 20        | 20 Python       | 8GB          | 100GB         |

---

## Monitoring

### Key Metrics

Monitor these metrics for performance insights:

| Metric                | Source               | Alert Threshold |
| --------------------- | -------------------- | --------------- |
| Request latency (p99) | `/metrics/api`       | > 500ms         |
| Execution time (p99)  | `/metrics/execution` | > 200ms         |
| Pool utilization      | `/metrics`           | > 80%           |
| Pool wait time        | `/metrics`           | > 100ms         |
| Redis latency         | Redis SLOWLOG        | > 10ms          |
| State size (avg)      | Logs                 | > 5MB           |

### Monitoring Endpoints

```bash
# Overall system metrics
curl https://localhost/metrics -H "x-api-key: $API_KEY"

# Execution-specific metrics
curl https://localhost/metrics/execution -H "x-api-key: $API_KEY"

# API request metrics
curl https://localhost/metrics/api -H "x-api-key: $API_KEY"

# Health with detailed timings
curl https://localhost/health/detailed -H "x-api-key: $API_KEY"
```

### Performance Alerts

Recommended alert conditions:

```yaml
# High latency
- condition: request_latency_p99 > 500ms
  duration: 5m
  severity: warning

# Pool exhaustion
- condition: pool_wait_time_avg > 100ms
  duration: 2m
  severity: critical

# State size growing
- condition: state_size_avg > 10MB
  duration: 1h
  severity: warning
```

---

## Troubleshooting

### High Latency

1. **Check pool utilization**:

   ```bash
   curl https://localhost/metrics | jq '.pool'
   ```

   If pool is frequently exhausted, increase `CONTAINER_POOL_PY`.

2. **Check Redis latency**:

   ```bash
   redis-cli --latency
   ```

   If > 10ms, consider Redis tuning or dedicated instance.

3. **Check REPL health**:
   ```bash
   curl https://localhost/health/detailed | jq '.repl'
   ```
   If unhealthy, check REPL server logs.

### Pool Exhaustion

1. **Increase pool size**:

   ```bash
   CONTAINER_POOL_PY=15
   ```

2. **Check for slow executions**:
   Long-running code blocks sandboxes. Consider timeout reduction:

   ```bash
   MAX_EXECUTION_TIME=15
   ```

3. **Check sandbox cleanup**:
   Sandboxes should be destroyed immediately. Check for stale sandbox directories:
   ```bash
   ls -la /var/lib/code-interpreter/sandboxes/
   ```

### Memory Issues

1. **Check API container memory**:

   ```bash
   docker stats --no-stream code-interpreter-api
   ```

2. **Reduce state size limit**:

   ```bash
   STATE_MAX_SIZE_MB=25
   ```

3. **Check for memory leaks in user code**:
   Review execution patterns for memory-intensive operations.

---

## Performance Testing

Run performance tests with the included script:

```bash
# Activate virtual environment
source .venv/bin/activate

# Install dependencies
pip install aiohttp

# Run performance tests
python scripts/perf_test.py
```

The script tests:

- Simple Python execution
- Complex Python execution
- Concurrent requests
- State persistence overhead
- File operations

---

## Related Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) - System architecture
- [CONFIGURATION.md](CONFIGURATION.md) - All configuration options
- [REPL.md](REPL.md) - REPL server details
- [STATE_PERSISTENCE.md](STATE_PERSISTENCE.md) - State persistence guide
