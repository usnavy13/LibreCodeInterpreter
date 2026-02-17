# Configuration Guide

This document provides comprehensive information about configuring the Code Interpreter API.

## Overview

The Code Interpreter API uses environment-based configuration with sensible defaults. All configuration options can be set via environment variables or a `.env` file.

## Quick Start

1. Copy the example environment file:

   ```bash
   cp .env.example .env
   ```

2. Edit `.env` with your specific settings:

   ```bash
   # At minimum, change the API key
   API_KEY=your-secure-api-key-here
   ```

3. Validate your configuration:
   ```bash
   python config_manager.py validate
   ```

## Configuration Sections

### API Configuration

Controls the basic API server settings.

| Variable     | Default   | Description                                          |
| ------------ | --------- | ---------------------------------------------------- |
| `PORT`       | `8000`    | External port the API is reachable on (docker-compose) |
| `API_HOST`   | `0.0.0.0` | Host to bind the API server                          |
| `API_DEBUG`  | `false`   | Enable debug mode (disable in production)            |
| `API_RELOAD` | `false`   | Enable auto-reload for development                   |

### SSL/HTTPS Configuration

Configures SSL/TLS support for secure HTTPS connections.

| Variable         | Default  | Description                                              |
| ---------------- | -------- | -------------------------------------------------------- |
| `ENABLE_HTTPS`   | `false`  | Enable HTTPS/SSL support                                 |
| `SSL_CERTS_PATH` | `./ssl`  | Host path to directory containing `cert.pem` and `key.pem` |

> **Note:** The certificate files are automatically mapped to `/app/ssl/` inside the API container via `docker-compose.yml`. You only need to set `SSL_CERTS_PATH` to point to your certificates directory on the host.

**HTTPS Setup:**

1. **Generate or obtain SSL certificates**:

   ```bash
   # For development (self-signed certificate)
   mkdir ssl
   openssl req -x509 -newkey rsa:4096 -nodes -out ssl/cert.pem -keyout ssl/key.pem -days 365

   # For production, use certificates from a trusted CA
   ```

2. **Configure HTTPS in .env**:

   ```bash
   ENABLE_HTTPS=true

   # If using the default ./ssl directory, no additional config needed.
   # If your certs are elsewhere, set the path:
   # SSL_CERTS_PATH=/path/to/your/ssl/certs
   ```

   The directory must contain files named `cert.pem` and `key.pem`.

3. **Deploy with docker compose**:
   ```bash
   docker compose up -d
   ```

**Security Notes:**

- Use certificates from trusted Certificate Authorities in production
- Keep private keys secure and never commit them to version control
- Consider using Let's Encrypt for free SSL certificates
### Authentication Configuration

Manages API key authentication and security.

| Variable            | Default        | Description                            |
| ------------------- | -------------- | -------------------------------------- |
| `API_KEY`           | `test-api-key` | Primary API key (CHANGE IN PRODUCTION) |
| `API_KEYS`          | -              | Additional API keys (comma-separated)  |

**Security Notes:**

- API keys should be at least 16 characters long
- Use cryptographically secure random keys in production
- Consider rotating API keys regularly

### Redis Configuration

Redis is used for session management and caching.

| Variable                       | Default     | Description                                        |
| ------------------------------ | ----------- | -------------------------------------------------- |
| `REDIS_HOST`                   | `localhost` | Redis server hostname                              |
| `REDIS_PORT`                   | `6379`      | Redis server port                                  |
| `REDIS_PASSWORD`               | -           | Redis password (if required)                       |
| `REDIS_DB`                     | `0`         | Redis database number                              |
| `REDIS_URL`                    | -           | Complete Redis URL (overrides individual settings) |
| `REDIS_MAX_CONNECTIONS`        | `20`        | Maximum connections in pool                        |
| `REDIS_SOCKET_TIMEOUT`         | `5`         | Socket timeout (seconds)                           |
| `REDIS_SOCKET_CONNECT_TIMEOUT` | `5`         | Connection timeout (seconds)                       |

**Example Redis URL:**

```
REDIS_URL=redis://password@localhost:6379/0
```

### MinIO/S3 Configuration

MinIO provides S3-compatible object storage for files.

| Variable           | Default                  | Description                         |
| ------------------ | ------------------------ | ----------------------------------- |
| `MINIO_ENDPOINT`   | `localhost:9000`         | MinIO server endpoint (no protocol) |
| `MINIO_ACCESS_KEY` | `minioadmin`             | MinIO access key                    |
| `MINIO_SECRET_KEY` | `minioadmin`             | MinIO secret key                    |
| `MINIO_SECURE`     | `false`                  | Use HTTPS for MinIO connections     |
| `MINIO_BUCKET`     | `code-interpreter-files` | Bucket name for file storage        |

### Sandbox Configuration

nsjail is used for secure code execution in isolated sandboxes.

| Variable                           | Default                                     | Description                           |
| ---------------------------------- | ------------------------------------------- | ------------------------------------- |
| `NSJAIL_BINARY`                    | `nsjail`                                    | Path to nsjail binary                 |
| `SANDBOX_BASE_DIR`                 | `/var/lib/code-interpreter/sandboxes`       | Base directory for sandbox filesystems |
| `SANDBOX_TMPFS_SIZE_MB`            | `100`                                       | tmpfs size for sandbox /tmp (MB)      |
| `SANDBOX_TTL_MINUTES`              | `5`                                         | Sandbox time-to-live                  |
| `SANDBOX_CLEANUP_INTERVAL_MINUTES` | `5`                                         | Cleanup check interval                |

**Security Notes:**

- nsjail provides PID, mount, and network namespace isolation
- Code runs as non-root user (uid 1001) inside the sandbox
- The API container requires `SYS_ADMIN` capability for nsjail namespace creation

### Resource Limits

#### Execution Limits

| Variable             | Default | Description                           |
| -------------------- | ------- | ------------------------------------- |
| `MAX_EXECUTION_TIME` | `30`    | Maximum code execution time (seconds) |
| `MAX_MEMORY_MB`      | `512`   | Maximum memory per execution (MB)     |

#### File Limits

| Variable                | Default | Description                        |
| ----------------------- | ------- | ---------------------------------- |
| `MAX_FILE_SIZE_MB`      | `10`    | Maximum individual file size (MB)  |
| `MAX_FILES_PER_SESSION` | `50`    | Maximum files per session          |
| `MAX_OUTPUT_FILES`      | `10`    | Maximum output files per execution |
| `MAX_FILENAME_LENGTH`   | `255`   | Maximum filename length            |

### Session Configuration

| Variable                           | Default | Description                  |
| ---------------------------------- | ------- | ---------------------------- |
| `SESSION_TTL_HOURS`                | `24`    | Session time-to-live (hours) |
| `SESSION_CLEANUP_INTERVAL_MINUTES` | `10`    | Cleanup interval (minutes)   |

### Sandbox Pool Configuration

Pre-warmed Python REPL sandboxes reduce execution latency by eliminating interpreter startup and library import time. Only Python supports REPL pooling; all other languages use one-shot nsjail execution.

| Variable                           | Default | Description                            |
| ---------------------------------- | ------- | -------------------------------------- |
| `SANDBOX_POOL_ENABLED`             | `true`  | Enable Python REPL pool                |
| `SANDBOX_POOL_WARMUP_ON_STARTUP`   | `true`  | Pre-warm Python REPLs at startup       |
| `SANDBOX_POOL_PY`                  | `5`     | Number of pre-warmed Python REPLs      |

**Note:** Sandboxes are destroyed immediately after execution. The pool is automatically replenished in the background. Non-Python languages do not use pooling.

### REPL Configuration (Python Fast Execution)

REPL mode keeps a Python interpreter running inside pooled sandboxes with common libraries pre-imported, reducing execution latency from ~3,500ms to ~20-40ms.

| Variable                      | Default | Description                             |
| ----------------------------- | ------- | --------------------------------------- |
| `REPL_ENABLED`                | `true`  | Enable pre-warmed Python REPL           |
| `REPL_WARMUP_TIMEOUT_SECONDS` | `15`    | Timeout for REPL server to become ready |

### State Persistence Configuration (Python)

Python sessions can persist variables, functions, and objects across executions using the `session_id` parameter.

| Variable                    | Default | Description                          |
| --------------------------- | ------- | ------------------------------------ |
| `STATE_PERSISTENCE_ENABLED` | `true`  | Enable Python state persistence      |
| `STATE_TTL_SECONDS`         | `7200`  | Redis hot storage TTL (2 hours)      |
| `STATE_CAPTURE_ON_ERROR`    | `false` | Save state even on execution failure |

### State Archival Configuration (Python)

Inactive states are automatically archived to MinIO for long-term storage.

| Variable                               | Default | Description                            |
| -------------------------------------- | ------- | -------------------------------------- |
| `STATE_ARCHIVE_ENABLED`                | `true`  | Enable MinIO cold storage archival     |
| `STATE_ARCHIVE_AFTER_SECONDS`          | `3600`  | Archive after this inactivity (1 hour) |
| `STATE_ARCHIVE_TTL_DAYS`               | `1`     | Keep archives for this many days (24h) |
| `STATE_ARCHIVE_CHECK_INTERVAL_SECONDS` | `300`   | Archival check frequency (5 min)       |

### Security Configuration

| Variable                      | Default | Description                             |
| ----------------------------- | ------- | --------------------------------------- |
| `ENABLE_NETWORK_ISOLATION`    | `true`  | Enable network isolation for sandboxes  |
| `ENABLE_FILESYSTEM_ISOLATION` | `true`  | Enable filesystem isolation             |

### Logging Configuration

| Variable               | Default | Description                                     |
| ---------------------- | ------- | ----------------------------------------------- |
| `LOG_LEVEL`            | `INFO`  | Logging level (DEBUG, INFO, WARNING, ERROR)     |
| `LOG_FORMAT`           | `json`  | Log format (`json` or `text`)                   |
| `LOG_FILE`             | -       | Log file path (stdout if not set)               |
| `LOG_MAX_SIZE_MB`      | `100`   | Maximum log file size (MB)                      |
| `LOG_BACKUP_COUNT`     | `5`     | Number of log file backups                      |
| `ENABLE_ACCESS_LOGS`   | `false` | Enable uvicorn HTTP access logs                 |
| `ENABLE_SECURITY_LOGS` | `true`  | Enable security event logs                      |

**Log level guide:**

- **`INFO`** (default) — Clean, readable output. Logs startup/shutdown lifecycle, one entry per code execution (request + response), session cleanup summaries, warnings, and errors. Internal details like sandbox creation, REPL warmup, state persistence, file operations, and pool replenishment are suppressed.
- **`DEBUG`** — Full detail. Adds per-request internals: sandbox acquisition, REPL readiness, state save/load, file mounting, session reuse lookups, pool warmup cycles, and all HTTP request/response logging.
- **`WARNING`** / **`ERROR`** — Only problems.

**Request logging:** The `RequestLoggingMiddleware` handles HTTP request logging with status-aware levels — 5xx responses log at ERROR, 4xx at WARNING, and 2xx/3xx at DEBUG. This replaces uvicorn's native access logs (disabled by default). Set `ENABLE_ACCESS_LOGS=true` to re-enable uvicorn's access logs if needed.

### Development Configuration

| Variable       | Default | Description                            |
| -------------- | ------- | -------------------------------------- |
| `ENABLE_CORS`  | `false` | Enable CORS (for development)          |
| `CORS_ORIGINS` | -       | Allowed CORS origins (comma-separated) |
| `ENABLE_DOCS`  | `true`  | Enable API documentation endpoints     |

## Language-Specific Configuration

All 12 language runtimes are pre-installed in the unified Docker image. No per-language images are needed.

### Supported Languages

- **Python** (`py`): Python 3.12 with numpy, pandas, matplotlib, scipy, sklearn, etc.
- **Node.js** (`js`): Node.js 22
- **TypeScript** (`ts`): Node.js 22 with TypeScript
- **Go** (`go`): Go 1.23
- **Java** (`java`): OpenJDK (default-jdk)
- **C** (`c`): GCC
- **C++** (`cpp`): G++
- **PHP** (`php`): PHP 8.3
- **Rust** (`rs`): Rust (stable)
- **R** (`r`): R with dplyr, ggplot2, data.table, etc.
- **Fortran** (`f90`): gfortran
- **D** (`d`): LDC

## Configuration Management Tools

### Command Line Tool

Use the configuration management script:

```bash
# Show configuration summary
python config_manager.py summary

# Validate configuration
python config_manager.py validate

# Check security settings
python config_manager.py security

# Generate complete .env template
python config_manager.py template

# Export configuration as JSON
python config_manager.py export
```

### Programmatic Access

```python
from src.config import settings
from src.utils.config_validator import validate_configuration

# Access configuration
print(f"API Port: {settings.api_port}")
print(f"Max Memory: {settings.max_memory_mb}MB")

# Validate configuration
if validate_configuration():
    print("Configuration is valid")
```

## Production Deployment Checklist

### Security

- [ ] Change default API key to a secure random value
- [ ] Enable network isolation (`ENABLE_NETWORK_ISOLATION=true`)
- [ ] Enable filesystem isolation (`ENABLE_FILESYSTEM_ISOLATION=true`)
- [ ] Ensure nsjail sandbox isolation is active
- [ ] Review and adjust resource limits

### Performance

- [ ] Set appropriate memory limits based on expected workload
- [ ] Configure Redis connection pooling
- [ ] Set reasonable execution timeouts
- [ ] Configure log rotation
- [ ] Enable REPL mode for Python (`REPL_ENABLED=true`)
- [ ] Configure sandbox pool size based on expected Python usage
- [ ] Review state persistence TTL settings

### State Persistence (Python)

- [ ] Configure `STATE_TTL_SECONDS` based on session patterns
- [ ] Enable state archival for long-term session resumption
- [ ] Configure archival TTL (`STATE_ARCHIVE_TTL_DAYS`)

### Monitoring

- [ ] Enable structured logging (`LOG_FORMAT=json`)
- [ ] Configure log aggregation
- [ ] Set up health check monitoring
- [ ] Enable security event logging

### Infrastructure

- [ ] Secure Redis with authentication
- [ ] Secure MinIO with proper access keys
- [ ] Ensure SYS_ADMIN capability is set for nsjail
- [ ] Set up backup for Redis and MinIO data

## Troubleshooting

### Configuration Validation Errors

Run the validation tool to identify issues:

```bash
python config_manager.py validate
```

### Common Issues

1. **Redis Connection Failed**
   - Check Redis server is running
   - Verify host, port, and credentials
   - Check network connectivity

2. **MinIO Connection Failed**
   - Verify MinIO server is accessible
   - Check access key and secret key
   - Ensure bucket exists or can be created

3. **Sandbox Execution Failed**
   - Verify nsjail binary is available
   - Check that the API container has SYS_ADMIN capability
   - Ensure sandbox base directory exists and is writable

4. **Resource Limit Errors**
   - Check system resources available
   - Adjust limits based on hardware
   - Monitor resource usage

### Debug Mode

Enable verbose logging for troubleshooting:

```bash
LOG_LEVEL=DEBUG              # Shows all internal operations (sandbox, REPL, state, files)
ENABLE_ACCESS_LOGS=true      # Re-enables uvicorn per-request access logs
API_DEBUG=true               # Enables /config endpoint and verbose error responses
```

**Warning:** Disable debug mode in production as it may expose sensitive information.

## Environment-Specific Configurations

### Development

```bash
API_DEBUG=true
API_RELOAD=true
ENABLE_CORS=true
ENABLE_DOCS=true
LOG_LEVEL=DEBUG
ENABLE_ACCESS_LOGS=true
```

### Testing

```bash
API_DEBUG=false
ENABLE_DOCS=true
LOG_LEVEL=INFO
MAX_EXECUTION_TIME=10
MAX_MEMORY_MB=256
```

### Production

```bash
API_DEBUG=false
API_RELOAD=false
ENABLE_CORS=false
ENABLE_DOCS=false
LOG_LEVEL=INFO
LOG_FORMAT=json
ENABLE_SECURITY_LOGS=true
# ENABLE_ACCESS_LOGS defaults to false — request logging middleware
# handles this with status-aware levels (errors at WARNING/ERROR)
```
