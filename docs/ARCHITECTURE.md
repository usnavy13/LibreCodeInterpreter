# Architecture Overview

This document provides a comprehensive overview of the Code Interpreter API architecture.

## System Architecture

```
                                    ┌─────────────────────────────────────────────────────────────┐
                                    │                      Code Interpreter API                   │
                                    │                                                             │
  ┌──────────┐    HTTPS/443         │  ┌─────────────┐    ┌─────────────────────────────────┐   │
  │  Client  │ ──────────────────────▶ │   FastAPI   │───▶│     ExecutionOrchestrator       │   │
  │(LibreChat│                      │  │  (main.py)  │    │       (orchestrator.py)         │   │
  │  or API) │ ◀──────────────────────│             │◀───│                                   │   │
  └──────────┘                      │  └─────────────┘    └─────────────────────────────────┘   │
                                    │         │                        │                         │
                                    │         ▼                        ▼                         │
                                    │  ┌─────────────┐    ┌─────────────────────────────────┐   │
                                    │  │ Middleware  │    │           Services               │   │
                                    │  │  - Auth     │    │  ┌─────────┐  ┌─────────────┐   │   │
                                    │  │  - Headers  │    │  │Container│  │  Execution  │   │   │
                                    │  │  - Logging  │    │  │  Pool   │  │   Runner    │   │   │
                                    │  │  - Metrics  │    │  └────┬────┘  └──────┬──────┘   │   │
                                    │  └─────────────┘    │       │              │          │   │
                                    │                     │       ▼              ▼          │   │
                                    │                     │  ┌──────────────────────────┐   │   │
                                    │                     │  │   Container Manager      │   │   │
                                    │                     │  │   + REPL Executor        │   │   │
                                    │                     │  └──────────────────────────┘   │   │
                                    │                     └─────────────────────────────────┘   │
                                    └────────────────────────────────┬──────────────────────────┘
                                                                     │
                          ┌──────────────────────────────────────────┼──────────────────────────────┐
                          │                                          │                              │
                          ▼                                          ▼                              ▼
                   ┌──────────────┐                           ┌──────────────┐               ┌──────────────┐
                   │    Redis     │                           │    Docker    │               │    MinIO     │
                   │              │                           │    Engine    │               │   (S3-API)   │
                   │ - Sessions   │                           │              │               │              │
                   │ - State      │                           │ ┌──────────┐ │               │ - Files      │
                   │ - Caching    │                           │ │Container │ │               │ - State      │
                   │              │                           │ │  Pool    │ │               │   Archives   │
                   └──────────────┘                           │ └──────────┘ │               └──────────────┘
                                                              └──────────────┘
```

## Core Components

### 1. API Layer (`src/api/`)

The API layer contains thin endpoint handlers that delegate to the orchestrator:

| File        | Purpose                                                       |
| ----------- | ------------------------------------------------------------- |
| `exec.py`   | Code execution endpoint, delegates to `ExecutionOrchestrator` |
| `files.py`  | File upload, download, and list operations                    |
| `health.py` | Health checks and metrics endpoints                           |

**Design principle:** Endpoints are intentionally thin (~70 lines each). All business logic resides in services.

### 2. Services Layer (`src/services/`)

Business logic is organized into focused services:

| Service                   | File                | Responsibility                   |
| ------------------------- | ------------------- | -------------------------------- |
| **ExecutionOrchestrator** | `orchestrator.py`   | Coordinates execution workflow   |
| **SessionService**        | `session.py`        | Redis session management         |
| **FileService**           | `file.py`           | MinIO file storage               |
| **StateService**          | `state.py`          | Internal Python state persistence (Redis, no external API) |
| **StateArchivalService**  | `state_archival.py` | Internal state archival (MinIO)           |
| **AuthService**           | `auth.py`           | API key authentication           |
| **HealthService**         | `health.py`         | Health checks                    |
| **MetricsService**        | `metrics.py`        | Metrics collection               |
| **CleanupService**        | `cleanup.py`        | Background cleanup tasks         |

### 3. Container Management (`src/services/container/`)

Container lifecycle is managed by a dedicated package:

| Component             | File               | Purpose                                            |
| --------------------- | ------------------ | -------------------------------------------------- |
| **ContainerManager**  | `manager.py`       | Container lifecycle (create, start, stop, destroy) |
| **ContainerPool**     | `pool.py`          | Pre-warmed container pool per language             |
| **ContainerExecutor** | `executor.py`      | Command execution in containers                    |
| **REPLExecutor**      | `repl_executor.py` | Python REPL communication                          |
| **DockerClient**      | `client.py`        | Docker client factory                              |

### 4. Execution Engine (`src/services/execution/`)

Code execution is handled by:

| Component           | File        | Purpose                                                    |
| ------------------- | ----------- | ---------------------------------------------------------- |
| **ExecutionRunner** | `runner.py` | Core execution logic, routes to REPL or standard execution |
| **OutputProcessor** | `output.py` | Output processing and validation                           |

### 5. Event Bus (`src/core/events.py`)

Services communicate via an async event bus to avoid circular dependencies:

```python
# Event types
class ExecutionCompleted(Event): ...
class ExecutionStarted(Event): ...
class SessionCreated(Event): ...
class SessionDeleted(Event): ...
class FileUploaded(Event): ...
class ContainerAcquiredFromPool(Event): ...
class PoolWarmedUp(Event): ...
```

**Usage:**

```python
# Subscribe to events
event_bus.subscribe(ExecutionCompleted, cleanup_handler)

# Publish events
await event_bus.publish(ExecutionCompleted(session_id=..., execution_id=...))
```

---

## Request Flows

### Code Execution Flow

```
1. Client POST /exec
       │
       ▼
2. AuthMiddleware validates API key
       │
       ▼
3. ExecutionOrchestrator.execute()
       │
       ├── 3a. Validate request (language, code size)
       │
       ├── 3b. Get/create session (SessionService)
       │
       ├── 3c. Load state if session_id provided (StateService)
       │
       ├── 3d. Upload input files to container
       │
       ├── 3e. Acquire container from pool
       │         │
       │         └── ContainerPool.acquire() → returns warm container
       │
       ├── 3f. Execute code
       │         │
       │         ├── Python + REPL: REPLExecutor.execute()
       │         │     └── Send JSON via Docker attach socket
       │         │
       │         └── Other languages: ContainerExecutor.execute()
       │               └── docker exec with timeout
       │
       ├── 3g. Save state if Python (StateService)
       │
       ├── 3h. Download output files from container
       │
       └── 3i. Destroy container immediately
       │
       ▼
4. Return ExecResponse with stdout, stderr, files, session_id
```

### File Upload Flow

```
1. Client POST /upload (multipart/form-data)
       │
       ▼
2. AuthMiddleware validates API key
       │
       ▼
3. FileService.upload()
       │
       ├── 3a. Validate file size and count
       │
       ├── 3b. Get/create session
       │
       └── 3c. Store file in MinIO
       │
       ▼
4. Return session_id and file_id
```

## Container Lifecycle

### Container Pool

The container pool pre-warms containers to eliminate cold start latency:

```
┌────────────────────────────────────────────────────────────────────────────┐
│                           Container Pool                                    │
├────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│   Python Pool (min: 5, max: 20)         JavaScript Pool (min: 2, max: 8)  │
│   ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐      ┌─────┐ ┌─────┐                    │
│   │REPL │ │REPL │ │REPL │ │REPL │      │ JS  │ │ JS  │                    │
│   │Ready│ │Ready│ │Ready│ │Ready│      │Ready│ │Ready│                    │
│   └─────┘ └─────┘ └─────┘ └─────┘      └─────┘ └─────┘                    │
│                                                                            │
│   Acquisition: O(1) ~3ms               Acquisition: O(1) ~3ms             │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘

Pool Lifecycle:
───────────────
1. On startup: Pre-warm containers to min pool size
2. On acquire: Pop container from pool, mark as in-use
3. On execution complete: Destroy container (no reuse)
4. Background: Replenish pool to min size when below threshold
```

### REPL Server

For Python, containers run a REPL server as PID 1:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Python Container                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   PID 1: repl_server.py                                                     │
│   ┌───────────────────────────────────────────────────────────────────┐    │
│   │  Pre-imported: numpy, pandas, matplotlib, scipy, sklearn, etc.   │    │
│   │                                                                   │    │
│   │  Namespace: { user variables, functions, objects }               │    │
│   │                                                                   │    │
│   │  Protocol: JSON-framed via stdin/stdout                          │    │
│   └───────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│   Communication: Docker attach socket (not exec)                            │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

REPL Execution (~20-40ms):
──────────────────────────
1. REPLExecutor sends JSON request via attach socket
2. REPL server executes code in namespace
3. REPL server captures stdout, stderr, files
4. REPL server sends JSON response back
5. REPLExecutor parses response
```

---

## Data Storage

### Redis

Redis stores ephemeral data with TTL-based expiration:

| Data Type   | Key Pattern            | TTL    | Purpose                       |
| ----------- | ---------------------- | ------ | ----------------------------- |
| Sessions    | `session:{session_id}` | 24h    | Session metadata              |
| State       | `state:{session_id}`   | 2h     | Python namespace (compressed) |
| Rate limits | `ratelimit:{key}`      | varies | API rate limiting             |

### MinIO (S3-Compatible)

MinIO stores persistent files and archived state:

| Bucket                   | Object Pattern               | TTL | Purpose               |
| ------------------------ | ---------------------------- | --- | --------------------- |
| `code-interpreter-files` | `{session_id}/{file_id}`     | 24h | User files            |
| `code-interpreter-files` | `state-archive/{session_id}` | 7d  | Archived Python state |

---

## Dependency Injection

Services are registered and injected via FastAPI's dependency system:

```python
# src/dependencies/services.py

def get_file_service() -> FileService:
    return FileService(minio_client)

def get_session_service() -> SessionService:
    return SessionService(redis_pool)

def get_state_service() -> StateService:
    return StateService(redis_pool)

# Usage in endpoints
@router.post("/exec")
async def execute(
    request: ExecRequest,
    file_service: FileService = Depends(get_file_service),
    session_service: SessionService = Depends(get_session_service),
):
    ...
```

---

## Configuration Hierarchy

```
Environment Variables (.env)
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         src/config/__init__.py                               │
│                         (Unified Settings Class)                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   Imports and merges:                                                       │
│   ├── api.py        → API settings (host, port, debug)                     │
│   ├── docker.py     → Docker settings (base_url, timeout)                  │
│   ├── redis.py      → Redis settings (host, port, pool)                    │
│   ├── minio.py      → MinIO settings (endpoint, credentials)               │
│   ├── security.py   → Security settings (isolation, headers)               │
│   ├── resources.py  → Resource limits (memory, cpu, timeout)               │
│   ├── logging.py    → Logging settings (level, format)                     │
│   └── languages.py  → Language configuration (images, multipliers)         │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
    settings = Settings()  # Single global instance
```

**Access patterns:**

```python
from src.config import settings

# Grouped access
settings.api.host
settings.redis.max_connections
settings.resources.max_memory_mb

# Flat access (backward compatible)
settings.api_host
settings.redis_max_connections
settings.max_memory_mb
```

---

## Security Architecture

### Container Isolation

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Container Security Layers                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   1. Network Isolation    : network_mode: none (no network access)         │
│   2. Filesystem Isolation : read_only: true, /tmp as tmpfs                 │
│   3. Capability Dropping  : cap_drop: ALL                                   │
│   4. Resource Limits      : memory, cpu, pids, file descriptors            │
│   5. Security Options     : no-new-privileges:true                          │
│   6. tmpfs Options        : noexec, nosuid                                  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Authentication

- All endpoints except `/health` require API key
- API key passed via `x-api-key` header
- Multiple keys supported via `API_KEYS` env var
- Key validation cached for performance

---

## Middleware Stack

```
Request → SecurityMiddleware → AuthMiddleware → LoggingMiddleware → MetricsMiddleware → Endpoint
                                                                                           │
Response ← SecurityMiddleware ← AuthMiddleware ← LoggingMiddleware ← MetricsMiddleware ←──┘
```

| Middleware           | Purpose                              |
| -------------------- | ------------------------------------ |
| `SecurityMiddleware` | Security headers, request validation |
| `AuthMiddleware`     | API key authentication               |
| `LoggingMiddleware`  | Request/response logging             |
| `MetricsMiddleware`  | Latency and request metrics          |

---

## Key Files Reference

| Component      | Primary File                              | Description                                      |
| -------------- | ----------------------------------------- | ------------------------------------------------ |
| FastAPI App    | `src/main.py`                             | Application entry point with lifespan management |
| Orchestrator   | `src/services/orchestrator.py`            | Execution workflow coordinator                   |
| Container Pool | `src/services/container/pool.py`          | Pre-warmed container management                  |
| REPL Executor  | `src/services/container/repl_executor.py` | Python REPL communication                        |
| REPL Server    | `docker/repl_server.py`                   | In-container Python REPL                         |
| State Service  | `src/services/state.py`                   | Python state persistence                         |
| Event Bus      | `src/core/events.py`                      | Async event-driven communication                 |
| Settings       | `src/config/__init__.py`                  | Unified configuration                            |
