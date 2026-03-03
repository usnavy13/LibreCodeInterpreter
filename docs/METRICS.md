# Detailed Usage Metrics

Track per-execution, per-language, and per-API-key metrics.

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `DETAILED_METRICS_ENABLED` | Enable detailed metrics | `true` |
| `METRICS_BUFFER_SIZE` | In-memory buffer size | `10000` |
| `METRICS_ARCHIVE_ENABLED` | Archive to MinIO | `true` |
| `METRICS_ARCHIVE_RETENTION_DAYS` | Archive retention | `90` days |

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /metrics/detailed` | Summary with language breakdown |
| `GET /metrics/by-language` | Per-language execution stats |
| `GET /metrics/by-api-key/{hash}` | Per-API-key usage |
| `GET /metrics/pool` | Sandbox pool hit rates |

## Tracked Metrics

**Per-execution:**
- Language, execution time, memory usage, status, files generated, sandbox source

**Per-language:**
- Execution count, error rates, average execution times

**Per-API-key:**
- Request counts, resource consumption

**Pool:**
- Hit rate, cold starts, exhaustion events

## Architecture

| File | Purpose |
|------|---------|
| `src/models/metrics.py` | DetailedExecutionMetrics, LanguageMetrics |
| `src/services/detailed_metrics.py` | Metrics collection service |
| `src/services/orchestrator.py` | Records metrics after execution |
