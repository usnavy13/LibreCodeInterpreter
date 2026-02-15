# Programmatic Tool Calling (`/exec/programmatic`) - Design Document

> **Status**: Future Feature - Not Yet Implemented
> **Last Updated**: December 2024
> **Source**: LibreChat `@librechat/agents` package (v3.0.40+)

## Overview

Programmatic Tool Calling enables Python code to orchestrate multiple agent tools within a single execution. Instead of the LLM making individual tool calls one at a time, it writes Python code that calls multiple tools, processes results, uses loops/conditionals, and runs tools in parallel.

**Key Benefit**: Reduces LLM round-trips and token usage by letting code handle complex multi-tool workflows.

---

## Table of Contents

1. [API Contract](#api-contract)
2. [Request/Response Types](#requestresponse-types)
3. [Protocol Flow](#protocol-flow)
4. [Backend Implementation Requirements](#backend-implementation-requirements)
5. [Python Execution Environment](#python-execution-environment)
6. [Tool Interception Mechanism](#tool-interception-mechanism)
7. [Continuation Token Management](#continuation-token-management)
8. [Error Handling](#error-handling)
9. [Security Considerations](#security-considerations)
10. [Implementation Recommendations](#implementation-recommendations)

---

## API Contract

### Endpoint

```
POST /exec/programmatic
```

### Headers

```http
Content-Type: application/json
User-Agent: LibreChat/1.0
X-API-Key: {api_key}
```

### Authentication

Same as `/exec` endpoint - uses `X-API-Key` header.

---

## Request/Response Types

### Initial Request

```typescript
interface ProgrammaticExecutionRequest {
  code: string; // Python code with tool calls
  tools: LCTool[]; // Filtered tool definitions
  session_id?: string; // Optional: for file persistence
  timeout?: number; // 1000-300000ms (default: 60000)
  files?: CodeEnvFile[]; // Optional: files from previous session
}

interface LCTool {
  name: string; // Tool identifier
  description?: string; // Tool description for Python docstrings
  parameters?: JsonSchema; // JSON Schema for tool parameters
}

interface CodeEnvFile {
  id: string; // File identifier
  name: string; // Original filename
  session_id: string; // Source session
}
```

### Response - Tool Call Required

When Python code invokes a tool, execution pauses and returns:

```typescript
interface ProgrammaticExecutionResponse {
  status: "tool_call_required";
  session_id: string;
  continuation_token: string; // CRITICAL: needed to resume
  tool_calls: PTCToolCall[];
}

interface PTCToolCall {
  id: string; // Unique call ID (e.g., "call_001")
  name: string; // Tool name to execute
  input: Record<string, any>; // Parameters passed to tool
}
```

### Continuation Request

After client executes tools, send results back:

```typescript
interface ContinuationRequest {
  continuation_token: string;
  tool_results: PTCToolResult[];
}

interface PTCToolResult {
  call_id: string; // Matches PTCToolCall.id
  result: any; // Tool execution result (JSON-serializable)
  is_error: boolean; // Whether execution failed
  error_message?: string; // Error details if is_error=true
}
```

### Response - Completed

When Python code finishes:

```typescript
interface ProgrammaticExecutionResponse {
  status: "completed";
  session_id: string;
  stdout: string;
  stderr: string;
  files?: FileRefs; // Generated files
}
```

### Response - Error

```typescript
interface ProgrammaticExecutionResponse {
  status: "error";
  session_id?: string;
  error: string; // Error message
  stdout?: string; // Partial output if available
  stderr?: string;
}
```

---

## Protocol Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ ROUND 1: Initial Execution                                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Client                              Server                                 │
│    │                                   │                                    │
│    │  POST /exec/programmatic          │                                    │
│    │  { code, tools, session_id }      │                                    │
│    │ ─────────────────────────────────>│                                    │
│    │                                   │  1. Create execution context       │
│    │                                   │  2. Generate async Python wrapper  │
│    │                                   │  3. Start Python execution         │
│    │                                   │  4. Hit tool call → PAUSE          │
│    │                                   │  5. Generate continuation_token    │
│    │                                   │                                    │
│    │  { status: 'tool_call_required',  │                                    │
│    │    continuation_token,            │                                    │
│    │    tool_calls: [...] }            │                                    │
│    │ <─────────────────────────────────│                                    │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│ ROUND 2-N: Continuation (max 20 rounds)                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Client                              Server                                 │
│    │                                   │                                    │
│    │  [Execute tools locally]          │                                    │
│    │                                   │                                    │
│    │  POST /exec/programmatic          │                                    │
│    │  { continuation_token,            │                                    │
│    │    tool_results: [...] }          │                                    │
│    │ ─────────────────────────────────>│                                    │
│    │                                   │  1. Lookup paused execution        │
│    │                                   │  2. Inject tool results            │
│    │                                   │  3. Resume Python execution        │
│    │                                   │  4. Hit next tool OR complete      │
│    │                                   │                                    │
│    │  { status: 'tool_call_required',  │  ← More tools needed               │
│    │    continuation_token,            │                                    │
│    │    tool_calls: [...] }            │                                    │
│    │ <─────────────────────────────────│                                    │
│    │                                   │                                    │
│    │         OR                        │                                    │
│    │                                   │                                    │
│    │  { status: 'completed',           │  ← Execution finished              │
│    │    stdout, stderr, files }        │                                    │
│    │ <─────────────────────────────────│                                    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Backend Implementation Requirements

### 1. Execution State Management

The server must maintain execution state between requests:

```python
@dataclass
class PausedExecution:
    id: str                           # continuation_token
    session_id: str
    sandbox_id: str                   # Keep sandbox alive
    python_state: bytes               # Pickled execution state OR
    execution_socket: Any             # Active socket connection
    pending_tool_calls: List[dict]
    created_at: datetime
    timeout: int
    round_trip_count: int
```

**Storage Options**:

- Redis with TTL (recommended)
- In-memory with cleanup task
- Sandbox stays running with paused coroutine

### 2. Python Environment Setup

The Python execution environment must:

1. **Auto-wrap code in async context** - Users write plain `await` calls
2. **Inject tool stubs** - Tools become async functions that trigger pause
3. **Capture stdout/stderr** - Buffer output across round-trips
4. **Handle imports** - Pre-import common libraries

### 3. Sandbox Lifecycle

Unlike `/exec`, sandboxes for programmatic execution must:

- **Stay alive** between round-trips
- Have **longer TTL** (match request timeout, up to 5 minutes)
- Be **cleaned up** on completion, error, or timeout
- Support **session file access** at `/mnt/data/`

### 4. Round-Trip Limits

- **Maximum**: 20 round-trips per execution
- **Timeout**: Per-execution (not per-round), default 60s, max 300s
- **Cleanup**: On timeout, return partial output + error status

---

## Python Execution Environment

### Code Wrapping

User code is automatically wrapped:

```python
# User writes:
data = await query_database(sql="SELECT * FROM users")
print(f"Found {len(data)} users")

# Backend wraps as:
import asyncio
import sys
from io import StringIO

# Inject tool stubs
async def query_database(**kwargs):
    return await __tool_call__("query_database", kwargs)

async def __main__():
    data = await query_database(sql="SELECT * FROM users")
    print(f"Found {len(data)} users")

# Execute
asyncio.run(__main__())
```

### Tool Stub Injection

For each tool in the request, generate an async stub:

```python
async def {normalized_name}(**kwargs):
    """
    {tool.description}

    Parameters: {tool.parameters}
    """
    return await __tool_call__("{original_name}", kwargs)
```

### Tool Name Normalization

Tool names are converted to valid Python identifiers:

| Original Name | Python Name   | Rule Applied                 |
| ------------- | ------------- | ---------------------------- |
| `get-weather` | `get_weather` | Replace `-` with `_`         |
| `my tool`     | `my_tool`     | Replace space with `_`       |
| `for`         | `for_tool`    | Reserved keyword + `_tool`   |
| `123data`     | `_123data`    | Prefix `_` for numeric start |

### Pre-imported Libraries

Match our REPL environment:

- `numpy`, `pandas`, `matplotlib`
- `json`, `re`, `datetime`, `asyncio`
- `scipy`, `sklearn` (if available)

---

## Tool Interception Mechanism

### Option A: Coroutine Suspension (Recommended)

Use Python's async/await to naturally pause execution:

```python
class ToolCallInterrupt(Exception):
    def __init__(self, tool_name: str, tool_input: dict, call_id: str):
        self.tool_name = tool_name
        self.tool_input = tool_input
        self.call_id = call_id

async def __tool_call__(name: str, kwargs: dict) -> Any:
    call_id = f"call_{uuid.uuid4().hex[:8]}"

    # Signal to the executor that we need a tool call
    raise ToolCallInterrupt(name, kwargs, call_id)
```

The executor catches this, stores state, and returns to client.

### Option B: Greenlet/Continuation

Use greenlet for more complex control flow:

```python
import greenlet

def tool_stub(name, kwargs):
    # Switch back to main greenlet with tool call info
    return greenlet.getcurrent().parent.switch({
        'type': 'tool_call',
        'name': name,
        'input': kwargs,
        'id': generate_call_id()
    })
```

### Option C: Code Transformation (AST)

Transform the code to checkpoint after each tool call:

```python
# Original
result = await tool(x=1)
print(result)

# Transformed
__checkpoint__(1)
result = await tool(x=1)
__checkpoint__(2, result=result)
print(result)
```

---

## Continuation Token Management

### Token Structure

```python
@dataclass
class ContinuationToken:
    execution_id: str        # UUID
    session_id: str
    round_trip: int
    expires_at: datetime
    signature: str           # HMAC for validation

def generate_token(execution: PausedExecution) -> str:
    payload = {
        'execution_id': execution.id,
        'session_id': execution.session_id,
        'round_trip': execution.round_trip_count,
        'expires_at': (datetime.utcnow() + timedelta(seconds=execution.timeout)).isoformat()
    }
    signature = hmac.new(SECRET_KEY, json.dumps(payload).encode(), 'sha256').hexdigest()
    payload['signature'] = signature
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
```

### Token Validation

```python
def validate_token(token: str) -> ContinuationToken:
    try:
        payload = json.loads(base64.urlsafe_b64decode(token))

        # Verify signature
        signature = payload.pop('signature')
        expected = hmac.new(SECRET_KEY, json.dumps(payload).encode(), 'sha256').hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise InvalidTokenError("Invalid signature")

        # Check expiry
        if datetime.fromisoformat(payload['expires_at']) < datetime.utcnow():
            raise TokenExpiredError("Token expired")

        return ContinuationToken(**payload)
    except Exception as e:
        raise InvalidTokenError(str(e))
```

### State Storage

```python
# Redis keys
EXECUTION_STATE_KEY = "ptc:execution:{execution_id}"
EXECUTION_TTL = 300  # 5 minutes max

async def store_execution_state(execution: PausedExecution):
    await redis.setex(
        EXECUTION_STATE_KEY.format(execution_id=execution.id),
        EXECUTION_TTL,
        pickle.dumps(execution)
    )

async def retrieve_execution_state(execution_id: str) -> PausedExecution:
    data = await redis.get(EXECUTION_STATE_KEY.format(execution_id=execution_id))
    if not data:
        raise ExecutionNotFoundError(f"Execution {execution_id} not found or expired")
    return pickle.loads(data)
```

---

## Error Handling

### Error Types

| Error             | HTTP Status | Response                                                                              |
| ----------------- | ----------- | ------------------------------------------------------------------------------------- |
| Invalid token     | 400         | `{"status": "error", "error": "Invalid continuation token"}`                          |
| Token expired     | 400         | `{"status": "error", "error": "Execution expired"}`                                   |
| Max round-trips   | 400         | `{"status": "error", "error": "Exceeded maximum round trips (20)"}`                   |
| Execution timeout | 408         | `{"status": "error", "error": "Execution timeout", "stdout": "...", "stderr": "..."}` |
| Python error      | 200         | `{"status": "error", "error": "...", "stderr": "..."}`                                |
| Tool not found    | 200         | Tool result with `is_error: true`                                                     |

### Graceful Degradation

- Return partial stdout/stderr on timeout
- Continue execution even if some tools error
- Clean up sandbox on any terminal state

---

## Security Considerations

### 1. Token Security

- **Sign tokens** with HMAC to prevent forgery
- **Include expiry** in token payload
- **Validate round-trip count** to prevent replay
- **Bind to session** to prevent cross-session attacks

### 2. Sandbox Isolation

- Same isolation as `/exec` (nsjail namespace isolation, seccomp, cgroups)
- **Longer lifetime** requires monitoring for resource abuse
- **Memory limits** still enforced
- Sandbox destroyed on completion/error/timeout

### 3. Tool Injection

- **Only inject requested tools** - don't expose all available tools
- **Validate tool names** against provided definitions
- **Sanitize tool inputs** before execution

### 4. State Storage

- **Encrypt sensitive state** in Redis if needed
- **Limit state size** to prevent memory exhaustion
- **Clean up expired state** aggressively

---

## Implementation Recommendations

### Phase 1: Basic Implementation

1. **New endpoint**: `POST /exec/programmatic`
2. **Request validation**: Pydantic models for all types
3. **Simple execution**: Single round-trip (no continuation)
4. **Tool stub generation**: Inject async functions

### Phase 2: Multi-Round Support

1. **Continuation tokens**: Generate and validate
2. **State storage**: Redis with TTL
3. **Sandbox persistence**: Keep alive between rounds
4. **Round-trip limits**: Enforce maximum 20

### Phase 3: Production Hardening

1. **Token security**: HMAC signing
2. **Timeout handling**: Graceful cleanup
3. **Monitoring**: Metrics for round-trips, timeouts, errors
4. **Load testing**: Concurrent multi-round executions

### Estimated Scope

| Component               | Files to Create/Modify                            |
| ----------------------- | ------------------------------------------------- |
| API endpoint            | `src/api/programmatic.py` (new)                   |
| Request/Response models | `src/models/programmatic.py` (new)                |
| Execution orchestrator  | `src/services/programmatic_orchestrator.py` (new) |
| State management        | `src/services/continuation.py` (new)              |
| Python wrapper          | `src/services/execution/python_wrapper.py` (new)  |
| Tool stub generator     | `src/services/execution/tool_stubs.py` (new)      |
| Tests                   | `tests/integration/test_programmatic.py` (new)    |

---

## Client Usage Example

From the LibreChat agents package:

```typescript
// Initial execution
const response = await fetch("/exec/programmatic", {
  method: "POST",
  headers: { "X-API-Key": apiKey, "Content-Type": "application/json" },
  body: JSON.stringify({
    code: `
      weather = await get_weather(city="SF")
      forecast = await get_forecast(city="SF", days=7)
      print(f"Current: {weather}, 7-day: {forecast}")
    `,
    tools: [
      {
        name: "get_weather",
        parameters: {
          type: "object",
          properties: { city: { type: "string" } },
        },
      },
      {
        name: "get_forecast",
        parameters: {
          type: "object",
          properties: { city: { type: "string" }, days: { type: "integer" } },
        },
      },
    ],
    timeout: 60000,
  }),
});

let result = await response.json();

// Handle tool calls in a loop
while (result.status === "tool_call_required") {
  const toolResults = await Promise.all(
    result.tool_calls.map(async (call) => {
      try {
        const toolResult = await executeToolLocally(call.name, call.input);
        return { call_id: call.id, result: toolResult, is_error: false };
      } catch (e) {
        return {
          call_id: call.id,
          result: null,
          is_error: true,
          error_message: e.message,
        };
      }
    }),
  );

  const continuation = await fetch("/exec/programmatic", {
    method: "POST",
    headers: { "X-API-Key": apiKey, "Content-Type": "application/json" },
    body: JSON.stringify({
      continuation_token: result.continuation_token,
      tool_results: toolResults,
    }),
  });

  result = await continuation.json();
}

// result.status is now 'completed' or 'error'
console.log(result.stdout);
```

---

## References

- [LibreChat Agents Repository](https://github.com/danny-avila/agents)
- `@librechat/agents/src/tools/ProgrammaticToolCalling.ts` - Client implementation
- `@librechat/agents/src/types/tools.ts` - Type definitions
- Added in commit `bbdebef` (December 5, 2025)
