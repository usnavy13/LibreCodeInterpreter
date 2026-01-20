# Python State Persistence Guide

This document describes the Python state persistence feature, which allows variables, functions, and objects to persist across executions within a session.

## Overview

By default, each code execution starts with a clean Python interpreter. With state persistence enabled, Python sessions can maintain state across multiple API calls, enabling:

- **Iterative development**: Build up variables and functions across requests
- **Long-running workflows**: Create data in one call, analyze in subsequent calls
- **ML pipelines**: Train models in one call, use for predictions in later calls

### Architecture

State persistence uses a hybrid storage architecture:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Hybrid State Storage                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   Hot Storage (Redis)                  Cold Storage (MinIO)                 │
│   ┌─────────────────────┐              ┌─────────────────────┐             │
│   │ TTL: 2 hours        │    Archive   │ TTL: 7 days         │             │
│   │ Access: ~1ms        │ ──────────▶  │ Access: ~50ms       │             │
│   │ State: compressed   │   (after     │ State: compressed   │             │
│   │        lz4 + base64 │   1 hour     │        lz4 + base64 │             │
│   └─────────────────────┘  inactive)   └─────────────────────┘             │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## How It Works

### Execution Flow with State

1. **First execution (no session_id)**:

   ```
   POST /exec {"lang": "py", "code": "x = 42"}

   → Container executes code
   → REPL server captures namespace: {"x": 42}
   → Namespace serialized with cloudpickle
   → Compressed with lz4 (~10x reduction)
   → Stored in Redis with 2-hour TTL
   → Response includes session_id
   ```

2. **Subsequent execution (with session_id)**:

   ```
   POST /exec {"lang": "py", "code": "print(x)", "session_id": "abc123"}

   → StateService loads state from Redis
   → If not in Redis, checks MinIO archives
   → State deserialized into REPL namespace
   → Code executes with existing variables
   → Updated state saved back to Redis
   ```

### Serialization

State is serialized using:

| Step         | Library       | Purpose                                                  |
| ------------ | ------------- | -------------------------------------------------------- |
| 1. Serialize | `cloudpickle` | Handles complex objects (lambdas, classes, numpy arrays) |
| 2. Compress  | `lz4`         | Fast compression (~10x size reduction)                   |
| 3. Encode    | `base64`      | Safe storage in Redis                                    |

**Why cloudpickle?** Standard `pickle` cannot serialize:

- Lambda functions
- Functions defined in `__main__`
- Closures
- Dynamically created classes

cloudpickle handles all these cases, making it ideal for interactive sessions.

### Archival Process

A background task runs every 5 minutes to archive inactive states:

```
CleanupService (every 5 min)
    │
    └── For each state in Redis:
            │
            ├── Check last access time
            │
            └── If inactive > 1 hour:
                    │
                    ├── Upload to MinIO (state-archive/{session_id})
                    │
                    └── Keep in Redis (will expire at 2 hours)
```

When a session resumes after Redis expiry:

1. StateService checks Redis → not found
2. StateArchivalService checks MinIO → found
3. State restored to Redis for fast future access

---

## Configuration

### State Persistence Settings

| Variable                    | Default | Description                          |
| --------------------------- | ------- | ------------------------------------ |
| `STATE_PERSISTENCE_ENABLED` | `true`  | Enable/disable state persistence     |
| `STATE_TTL_SECONDS`         | `7200`  | Redis TTL (default 2 hours)          |
| `STATE_MAX_SIZE_MB`         | `50`    | Maximum serialized state size        |
| `STATE_CAPTURE_ON_ERROR`    | `false` | Save state even on execution failure |

### State Archival Settings

| Variable                               | Default | Description                            |
| -------------------------------------- | ------- | -------------------------------------- |
| `STATE_ARCHIVE_ENABLED`                | `true`  | Enable MinIO archival                  |
| `STATE_ARCHIVE_AFTER_SECONDS`          | `3600`  | Archive after this inactivity (1 hour) |
| `STATE_ARCHIVE_TTL_DAYS`               | `1`     | Keep archives for this many days (24h) |
| `STATE_ARCHIVE_CHECK_INTERVAL_SECONDS` | `300`   | Check frequency (5 minutes)            |

### Disabling State Persistence

To disable state persistence entirely:

```bash
STATE_PERSISTENCE_ENABLED=false
```

When disabled:

- Each Python execution starts with a clean namespace
- No state is saved to Redis or MinIO
- `session_id` in requests is ignored for state (still used for files)

---

## Usage Examples

### Basic Usage

```bash
# First request - creates session and variables
curl -sk -X POST https://localhost/exec \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d '{
    "lang": "py",
    "code": "x = [1, 2, 3]\ndef add(a, b): return a + b",
    "entity_id": "test",
    "user_id": "user1"
  }'

# Response:
# {
#   "session_id": "abc123...",
#   "stdout": "",
#   "stderr": "",
#   "exit_code": 0
# }

# Second request - reuses session and state
curl -sk -X POST https://localhost/exec \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d '{
    "lang": "py",
    "code": "print(sum(x), add(10, 20))",
    "entity_id": "test",
    "user_id": "user1",
    "session_id": "abc123..."
  }'

# Response:
# {
#   "stdout": "6 30\n",
#   "stderr": "",
#   "exit_code": 0
# }
```

### Working with Data

```bash
# Create a DataFrame
curl -sk -X POST https://localhost/exec \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d '{
    "lang": "py",
    "code": "import pandas as pd\ndf = pd.DataFrame({\"a\": [1,2,3], \"b\": [4,5,6]})",
    "entity_id": "test",
    "user_id": "user1"
  }'
# Returns session_id

# Query the DataFrame in next request
curl -sk -X POST https://localhost/exec \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d '{
    "lang": "py",
    "code": "print(df.describe())",
    "entity_id": "test",
    "user_id": "user1",
    "session_id": "<session_id from above>"
  }'
```

### ML Model Training

```bash
# Train a model
curl -sk -X POST https://localhost/exec \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d '{
    "lang": "py",
    "code": "from sklearn.linear_model import LinearRegression\nimport numpy as np\nX = np.array([[1],[2],[3]])\ny = np.array([1,2,3])\nmodel = LinearRegression().fit(X, y)",
    "entity_id": "test",
    "user_id": "user1"
  }'

# Use model for predictions
curl -sk -X POST https://localhost/exec \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d '{
    "lang": "py",
    "code": "print(model.predict([[4], [5]]))",
    "entity_id": "test",
    "user_id": "user1",
    "session_id": "<session_id>"
  }'
# Output: [4. 5.]
```

---

## What Persists

### Supported Types

| Type               | Examples                              | Notes                                 |
| ------------------ | ------------------------------------- | ------------------------------------- |
| Primitives         | `int`, `float`, `str`, `bool`, `None` | Fully supported                       |
| Collections        | `list`, `dict`, `set`, `tuple`        | Fully supported                       |
| NumPy arrays       | `np.array([1,2,3])`                   | Serialized efficiently                |
| Pandas objects     | `DataFrame`, `Series`                 | Serialized efficiently                |
| User functions     | `def foo(): ...`                      | Including closures                    |
| User classes       | `class MyClass: ...`                  | Including instances                   |
| Sklearn models     | `LinearRegression()`, etc.            | Trained state preserved               |
| Matplotlib figures | `plt.figure()`                        | As object (use `savefig()` for files) |

### What Does NOT Persist

| Type                          | Reason                           |
| ----------------------------- | -------------------------------- |
| Open file handles             | Cannot serialize OS resources    |
| Network connections           | Cannot serialize sockets         |
| Running threads/processes     | Cannot serialize execution state |
| Module-level state            | Imports reset each execution     |
| Generator state               | Cannot serialize iteration state |
| Compiled regex with callbacks | Cannot serialize C extensions    |

### Edge Cases

**Modules imported at session start:**

```python
# Session 1
import pandas as pd
df = pd.DataFrame({"a": [1,2,3]})

# Session 2 (same session_id)
print(df)  # Works! df is restored
print(pd)  # Error! pd must be re-imported
```

**Solution:** Re-import modules in each execution, or assign to variables:

```python
# Session 1
import pandas
pd = pandas  # Now pd is in namespace
```

---

## Technical Details

### REPL Server Implementation

The REPL server (`docker/repl_server.py`) handles serialization:

```python
# After code execution
namespace = {k: v for k, v in globals().items()
             if not k.startswith('_') and k not in BUILTIN_NAMES}

# Serialize
state_bytes = cloudpickle.dumps(namespace)
compressed = lz4.frame.compress(state_bytes)
encoded = base64.b64encode(compressed).decode('utf-8')

# Return in response
{"stdout": "...", "state": encoded}
```

### State Size Limits

The maximum state size is configurable via `STATE_MAX_SIZE_MB` (default 50MB).

If state exceeds this limit:

1. A warning is logged
2. State is NOT saved
3. Execution still succeeds
4. Next execution starts fresh

**Common causes of large state:**

- Large datasets loaded into memory
- Many trained ML models
- Cached computation results

**Solutions:**

- Save large data to files instead of variables
- Clear unused variables: `del large_variable`
- Increase limit if needed: `STATE_MAX_SIZE_MB=100`

### Storage Keys

| Storage | Key Pattern                  | Content                     |
| ------- | ---------------------------- | --------------------------- |
| Redis   | `state:{session_id}`         | Compressed state + metadata |
| MinIO   | `state-archive/{session_id}` | Compressed state (archived) |

---

## Performance Considerations

### Serialization Overhead

| State Size | Serialize | Compress | Total Overhead |
| ---------- | --------- | -------- | -------------- |
| 1 KB       | ~1ms      | ~0.1ms   | ~1ms           |
| 100 KB     | ~5ms      | ~1ms     | ~6ms           |
| 1 MB       | ~20ms     | ~5ms     | ~25ms          |
| 10 MB      | ~150ms    | ~40ms    | ~190ms         |

**Recommendation:** Keep state under 1MB for minimal latency impact.

### Compression Ratio

lz4 typically achieves:

- Python objects: 5-10x compression
- NumPy arrays: 2-5x compression (depends on data)
- Pandas DataFrames: 3-8x compression

### Memory Usage

During serialization, memory temporarily doubles:

- Original object in memory
- Serialized copy being created

Ensure containers have sufficient memory for state operations.

---

## Troubleshooting

### State Not Persisting

1. **Check if enabled:**

   ```bash
   # Verify setting
   echo $STATE_PERSISTENCE_ENABLED  # Should be "true"
   ```

2. **Check session_id:**
   - Ensure you're passing the `session_id` from the first response
   - Session IDs are case-sensitive

3. **Check state size:**
   - Large states may exceed `STATE_MAX_SIZE_MB`
   - Check logs for "State size exceeds limit" warnings

### State Restored but Variables Missing

1. **Module imports:**
   - Imported modules don't persist; re-import each execution

2. **Builtin overrides:**
   - Variables named after builtins may not persist

3. **Private variables:**
   - Variables starting with `_` are excluded

### Redis Connection Issues

```bash
# Check Redis connectivity
curl -X GET https://localhost/health/redis \
  -H "x-api-key: $API_KEY"
```

### Archive Not Working

1. **Check MinIO connectivity:**

   ```bash
   curl -X GET https://localhost/health/minio \
     -H "x-api-key: $API_KEY"
   ```

2. **Check archival settings:**
   ```bash
   echo $STATE_ARCHIVE_ENABLED        # Should be "true"
   echo $STATE_ARCHIVE_AFTER_SECONDS  # Default 3600
   ```

---

## Client-Side State API

Clients can download, cache, and restore state independently. This enables:

- **Longer state retention**: Cache state client-side beyond 2-hour Redis TTL
- **Reduced server load**: Restore from client cache instead of MinIO archive
- **Offline resilience**: Resume sessions even if server state is lost

### API Endpoints

| Endpoint                   | Method | Description                       |
| -------------------------- | ------ | --------------------------------- |
| `/state/{session_id}`      | GET    | Download state as raw lz4 binary  |
| `/state/{session_id}`      | POST   | Upload state as raw lz4 binary    |
| `/state/{session_id}/info` | GET    | Get state metadata                |
| `/state/{session_id}`      | DELETE | Delete state (always returns 204) |

### ExecResponse State Fields

Python executions return additional state fields:

```json
{
  "session_id": "abc123...",
  "stdout": "...",
  "has_state": true,
  "state_size": 1234,
  "state_hash": "sha256..."
}
```

| Field        | Type   | Description                                     |
| ------------ | ------ | ----------------------------------------------- |
| `has_state`  | `bool` | True when execution produced serializable state |
| `state_size` | `int`  | Size of compressed state in bytes               |
| `state_hash` | `str`  | SHA256 hash for change detection                |

### Downloading State

```bash
# Download state for client-side caching
curl -X GET https://localhost/state/{session_id} \
  -H "x-api-key: $API_KEY" \
  -o state.bin

# Response: Raw lz4 binary with ETag header
# ETag: "sha256hash..."
```

### Checking State Existence

```bash
curl -X GET https://localhost/state/{session_id}/info \
  -H "x-api-key: $API_KEY"

# Response:
{
  "exists": true,
  "session_id": "abc123...",
  "size_bytes": 1234,
  "hash": "sha256...",
  "created_at": "2024-01-01T12:00:00Z",
  "expires_at": "2024-01-01T14:00:00Z",
  "source": "redis"  // or "archive"
}
```

### Uploading State (Restore from Client Cache)

```bash
# Upload cached state before execution
curl -X POST https://localhost/state/{session_id} \
  -H "x-api-key: $API_KEY" \
  -H "Content-Type: application/octet-stream" \
  --data-binary @state.bin

# Response: 201 Created
{
  "message": "state_uploaded",
  "size": 1234
}
```

### ETag Caching

Use `If-None-Match` to avoid unnecessary downloads:

```bash
# Check if state changed since last download
curl -X GET https://localhost/state/{session_id} \
  -H "x-api-key: $API_KEY" \
  -H "If-None-Match: \"sha256hash...\""

# Response: 304 Not Modified (if unchanged)
```

### Client-Side Caching Workflow

```
1. Execute code → Response includes has_state, state_hash
2. Download state → GET /state/{session_id} → Cache locally
3. Before next execution:
   a. Check server → GET /state/{session_id}/info
   b. If exists=false AND client has cached state:
      Upload state → POST /state/{session_id}
   c. Execute → POST /exec with session_id
4. State is restored, execution continues
```

### Upload Priority

When a client uploads state, it takes priority over server-side state for the next 30 seconds. This ensures the client's cached state is used even if server had stale data.

### Error Responses

| Status | Error             | Description                                     |
| ------ | ----------------- | ----------------------------------------------- |
| 400    | `invalid_state`   | State format invalid (wrong version, too short) |
| 404    | `state_not_found` | No state exists for session                     |
| 413    | `state_too_large` | State exceeds 50MB limit                        |

### Example: Full Restore Flow

```bash
# 1. Execute and create state
RESPONSE=$(curl -sk -X POST https://localhost/exec \
  -H "x-api-key: $API_KEY" \
  -d '{"lang": "py", "code": "secret = 42"}')
SESSION_ID=$(echo $RESPONSE | jq -r '.session_id')

# 2. Download state for caching
curl -sk -X GET "https://localhost/state/$SESSION_ID" \
  -H "x-api-key: $API_KEY" -o /tmp/state.bin

# ... time passes, Redis TTL expires ...

# 3. Check if state exists
INFO=$(curl -sk -X GET "https://localhost/state/$SESSION_ID/info" \
  -H "x-api-key: $API_KEY")
EXISTS=$(echo $INFO | jq -r '.exists')

# 4. Restore if needed
if [ "$EXISTS" = "false" ]; then
  curl -sk -X POST "https://localhost/state/$SESSION_ID" \
    -H "x-api-key: $API_KEY" \
    -H "Content-Type: application/octet-stream" \
    --data-binary @/tmp/state.bin
fi

# 5. Execute with restored state
curl -sk -X POST https://localhost/exec \
  -H "x-api-key: $API_KEY" \
  -d "{\"lang\": \"py\", \"code\": \"print(secret)\", \"session_id\": \"$SESSION_ID\"}"
# Output: 42
```

---

## Related Documentation

- [CONFIGURATION.md](CONFIGURATION.md) - All configuration options
- [ARCHITECTURE.md](ARCHITECTURE.md) - System architecture overview
- [REPL.md](REPL.md) - REPL server details
- [PERFORMANCE.md](PERFORMANCE.md) - Performance tuning
