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

`docker-compose.yml` uses the following HTTPS contract:

- `PORT` is the external host port published by Docker.
- `SSL_CERTS_PATH` is a host path mounted into the API container at `/app/ssl`.
- `SSL_CERT_FILE` and `SSL_KEY_FILE` are paths inside the container.
- For predictable restarts, set `ENABLE_HTTPS=true` explicitly instead of relying on auto-detection.

| Variable         | Default                | Description |
| ---------------- | ---------------------- | ----------- |
| `PORT`           | `8000`                 | External host port published by Docker Compose |
| `ENABLE_HTTPS`   | auto                   | When unset, HTTPS auto-enables only if the configured cert and key files exist inside the container |
| `SSL_CERTS_PATH` | `./ssl`                | Host path mounted into the container at `/app/ssl` |
| `SSL_CERT_FILE`  | `/app/ssl/fullchain.pem` | Certificate file path inside the container |
| `SSL_KEY_FILE`   | `/app/ssl/privkey.pem` | Private key file path inside the container |
| `SSL_CA_CERTS`   | -                      | Optional CA bundle path inside the container |

**HTTPS Setup:**

1. **Use a simple cert directory on the host**:

   ```bash
   mkdir -p ssl
   openssl req -x509 -newkey rsa:4096 -nodes \
     -out ssl/fullchain.pem \
     -keyout ssl/privkey.pem \
     -days 365
   ```

   Then set:

   ```bash
   PORT=443
   ENABLE_HTTPS=true
   SSL_CERTS_PATH=./ssl
   SSL_CERT_FILE=/app/ssl/fullchain.pem
   SSL_KEY_FILE=/app/ssl/privkey.pem
   ```

2. **Use Let's Encrypt from the host**:

   If the host already has certificates in `/etc/letsencrypt`, mount that tree and point the app at the files inside `/app/ssl`:

   ```bash
   PORT=443
   ENABLE_HTTPS=true
   SSL_CERTS_PATH=/etc/letsencrypt
   SSL_CERT_FILE=/app/ssl/live/example.com/fullchain.pem
   SSL_KEY_FILE=/app/ssl/live/example.com/privkey.pem
   ```

3. **Start the stack**:

   ```bash
   docker compose up -d
   ```

4. **Verify HTTPS**:

   ```bash
   curl -fsk https://localhost/health
   ```

If you terminate TLS at an external reverse proxy instead, keep the API on HTTP by leaving `ENABLE_HTTPS` unset or setting it to `false`, and publish the proxy on `443` instead of the API container.

**Security Notes:**

- Use certificates from trusted Certificate Authorities in production
- Keep private keys secure and never commit them to version control
- Consider using Let's Encrypt for free SSL certificates
### Authentication Configuration

Manages API key authentication and security.

| Variable         | Default        | Description                                                      |
| ---------------- | -------------- | ---------------------------------------------------------------- |
| `API_KEY`        | `test-api-key` | Primary API key (CHANGE IN PRODUCTION)                           |
| `API_KEYS`       | -              | Additional API keys (comma-separated)                            |
| `MASTER_API_KEY` | -              | Required for `/api/v1/admin/*` endpoints                         |
| `AUTH_ENABLED`   | `true`         | When `false`, skip x-api-key/Basic checks on user endpoints      |

**How clients authenticate** (any one of):

1. **`x-api-key` header** — `x-api-key: <key>`. The traditional way. Reverse proxies that inject this header continue to work.
2. **HTTP Basic in URL credentials** — `Authorization: Basic base64("<key>:")`. Current LibreChat versions use this when `LIBRECHAT_CODE_BASEURL=https://<key>@your-api/v1` — `axios` and `node-fetch` automatically convert URL credentials into the Basic header. Single-token convention (Stripe / DigitalOcean / GitHub PAT style): the API key goes in the username slot, password is empty.
3. **`AUTH_ENABLED=false`** — no client-side auth. Use only when running on a trusted private network or behind another auth layer (mTLS, reverse-proxy auth, etc.).

When both `x-api-key` and a Basic header are present, `x-api-key` wins. This is deterministic for proxy-injection setups.

`/api/v1/admin/*` and the admin dashboard's API calls **always** require `MASTER_API_KEY`, regardless of `AUTH_ENABLED`.

**Rate limiting:** per-key rate limits and the IP-based auth-failure limiter both run inside the auth path. When `AUTH_ENABLED=false`, both are bypassed — your network boundary is responsible for any abuse protection.

**Security Notes:**

- API keys should be at least 16 characters long
- Use cryptographically secure random keys in production
- Consider rotating API keys regularly
- Setting `AUTH_ENABLED=false` opens user endpoints to anyone who can reach the URL — do not expose to the public internet without a proxy/VPN/mTLS in front

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

### S3 Configuration

S3-compatible object storage for files and archived state. The default deployment uses Garage; any S3-compatible backend (AWS S3, MinIO, Cloudflare R2, etc.) works.

| Variable         | Default                  | Description                            |
| ---------------- | ------------------------ | -------------------------------------- |
| `S3_ENDPOINT`    | `localhost:3900`         | S3 endpoint (host:port, no protocol)   |
| `S3_ACCESS_KEY`  | `test-access-key`        | S3 access key                          |
| `S3_SECRET_KEY`  | `test-secret-key`        | S3 secret key                          |
| `S3_SECURE`      | `false`                  | Use HTTPS for S3 connections           |
| `S3_BUCKET`      | `code-interpreter-files` | Bucket name for file storage           |
| `S3_REGION`      | `garage`                 | S3 region (set to match your backend)  |

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
- Code runs as a shared non-root UID inside the sandbox
- All sandbox languages default to UID `1001`, and can be moved with `SANDBOX_UID`
- The API container requires `SYS_ADMIN` capability for nsjail namespace creation

### Resource Limits

#### Execution Limits

| Variable             | Default | Description                           |
| -------------------- | ------- | ------------------------------------- |
| `MAX_EXECUTION_TIME` | `120`   | Maximum code execution time (seconds) |
| `MAX_MEMORY_MB`      | `512`   | Maximum memory per execution (MB)     |

#### File Limits

| Variable                | Default | Description                                                  |
| ----------------------- | ------- | ------------------------------------------------------------ |
| `MAX_FILE_SIZE_MB`      | `100`   | Maximum individual file size (MB)                            |
| `MAX_FILES_PER_SESSION` | `300`   | Maximum files per session (sized for skill bundles like pptx)|
| `MAX_OUTPUT_FILES`      | `10`    | Maximum output files per execution                           |
| `MAX_FILENAME_LENGTH`   | `255`   | Maximum filename length                                      |

### Session Configuration

| Variable                           | Default | Description                                                                |
| ---------------------------------- | ------- | -------------------------------------------------------------------------- |
| `SESSION_TTL_HOURS`                | `24`    | Session time-to-live (hours)                                               |
| `SESSION_CLEANUP_INTERVAL_MINUTES` | `60`    | Cleanup interval (minutes)                                                 |
| `ENABLE_ORPHAN_S3_CLEANUP`         | `true`  | Reap S3 objects with no matching session metadata during cleanup sweeps    |

### Sandbox Pool Configuration

Pre-warmed Python REPL sandboxes reduce execution latency by eliminating interpreter startup and library import time. Only Python supports REPL pooling; all other languages use one-shot nsjail execution.

| Variable                           | Default | Description                                              |
| ---------------------------------- | ------- | -------------------------------------------------------- |
| `SANDBOX_POOL_ENABLED`             | `true`  | Enable Python REPL pool                                  |
| `SANDBOX_POOL_WARMUP_ON_STARTUP`   | `true`  | Pre-warm Python REPLs at startup                         |
| `SANDBOX_POOL_PY`                  | `2`     | Number of pre-warmed Python REPLs                        |
| `SANDBOX_POOL_PARALLEL_BATCH`      | `5`     | Number of warmup sandboxes started concurrently          |
| `SANDBOX_POOL_REPLENISH_INTERVAL`  | `2`     | Seconds between pool replenishment checks                |
| `SANDBOX_POOL_EXHAUSTION_TRIGGER`  | `true`  | Trigger immediate replenishment when pool is exhausted   |
| `SANDBOX_UID`                      | `1001`  | Shared host UID used by all sandbox languages            |

**Note:** Sandboxes are destroyed immediately after execution. The pool is automatically replenished in the background. Non-Python languages do not use pooling.

### REPL Configuration (Python Fast Execution)

REPL mode keeps a Python interpreter running inside pooled sandboxes with common libraries pre-imported, reducing execution latency from ~3,500ms to ~20-40ms.

| Variable                      | Default | Description                             |
| ----------------------------- | ------- | --------------------------------------- |
| `REPL_ENABLED`                | `true`  | Enable pre-warmed Python REPL           |
| `REPL_WARMUP_TIMEOUT_SECONDS` | `15`    | Timeout for REPL server to become ready |

### State Persistence Configuration (Python)

Python `/exec` sessions can persist variables, functions, and objects across executions when a Python session is reused. The most explicit path is sending the prior `session_id`, but the backend can also reuse an existing session through same-user file references or `entity_id`.

| Variable                    | Default | Description                                                              |
| --------------------------- | ------- | ------------------------------------------------------------------------ |
| `STATE_PERSISTENCE_ENABLED` | `true`  | Enable Python state persistence                                          |
| `STATE_TTL_SECONDS`         | `7200`  | Redis hot storage TTL (2 hours)                                          |
| `STATE_CAPTURE_ON_ERROR`    | `false` | Save state even on execution failure                                     |
| `STATE_MAX_REDIS_SIZE_MB`   | `100`   | Max raw state size (MB) stored in Redis. Larger states go directly to S3 |

### State Archival Configuration (Python)

Inactive states are automatically archived to S3 for long-term storage.

| Variable                               | Default | Description                            |
| -------------------------------------- | ------- | -------------------------------------- |
| `STATE_ARCHIVE_ENABLED`                | `true`  | Enable S3 cold storage archival        |
| `STATE_ARCHIVE_AFTER_SECONDS`          | `3600`  | Archive after this inactivity (1 hour) |
| `STATE_ARCHIVE_TTL_DAYS`               | `1`     | Keep archives for this many days (24h) |
| `STATE_ARCHIVE_CHECK_INTERVAL_SECONDS` | `300`   | Archival check frequency (5 min)       |

### Security Configuration

| Variable                      | Default | Description                             |
| ----------------------------- | ------- | --------------------------------------- |
| `ENABLE_NETWORK_ISOLATION`    | `true`  | Enable network isolation for sandboxes  |
| `ENABLE_FILESYSTEM_ISOLATION` | `true`  | Enable filesystem isolation             |

### Sandbox Network Access (Skill Installs)

Off by default — sandboxes have no network access. When enabled, an inline allowlist HTTPS proxy on `127.0.0.1` lets sandboxes reach **only** package registries (PyPI, npm, Go modules, crates.io). Required for "skills" that `pip install` / `npm install` / `go get` / `cargo install` dependencies at runtime.

| Variable                   | Default               | Description                                                                       |
| -------------------------- | --------------------- | --------------------------------------------------------------------------------- |
| `ENABLE_SANDBOX_NETWORK`   | `false`               | Allow sandboxes to reach the internet via the inline allowlist proxy              |
| `SANDBOX_EGRESS_PORT`      | `18443`               | Port the inline egress proxy binds to on `127.0.0.1`                              |
| `SANDBOX_EGRESS_ALLOWLIST` | (registries default)  | Comma-separated list of additional hostnames the proxy permits                    |
| `SKILL_DEPS_PATH`          | `/opt/skill-deps`     | Host-side directory mounted into every sandbox so install caches compound across runs |

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

All 13 language runtimes are pre-installed in the unified Docker image. No per-language images are needed.

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
- **Bash** (`bash`): GNU Bash

## Programmatic Access

```python
from src.config import settings

# Flat access (backward compatible)
print(f"API Port: {settings.api_port}")
print(f"Max Memory: {settings.max_memory_mb}MB")

# Grouped access
print(f"S3 endpoint: {settings.s3.endpoint_url}")
print(f"Redis URL: {settings.redis.get_url()}")
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
- [ ] Secure S3 storage with proper access keys
- [ ] Ensure SYS_ADMIN capability is set for nsjail
- [ ] Set up backup for Redis and S3 data

## Troubleshooting

### Common Issues

1. **Redis Connection Failed**
   - Check Redis server is running
   - Verify host, port, and credentials
   - Check network connectivity

2. **S3 Connection Failed**
   - Verify S3 endpoint is accessible
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
