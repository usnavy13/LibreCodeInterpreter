# Testing Guide

This document describes the testing infrastructure, test organization, and how to run tests for the Code Interpreter API.

## Test Organization

Tests are organized into two main categories:

```
tests/
├── conftest.py              # Shared fixtures for all tests
├── unit/                    # Unit tests (no external dependencies)
│   ├── test_execution_service.py
│   ├── test_session_service.py
│   └── ...
├── integration/             # Integration tests (require running API, Redis, MinIO)
│   ├── test_api_contracts.py
│   ├── test_librechat_compat.py
│   ├── test_container_behavior.py
│   ├── test_session_behavior.py
│   ├── test_session_isolation.py
│   ├── test_session_state.py
│   └── test_file_handling.py
└── snapshots/               # Snapshot data for tests
```

### Unit Tests (`tests/unit/`)

Unit tests validate individual components in isolation:

- Mock external dependencies (Redis, MinIO, sandbox executor)
- Fast execution (~seconds)
- No infrastructure required

### Integration Tests (`tests/integration/`)

Integration tests validate end-to-end behavior:

- Require running API, Redis, MinIO
- Test actual API endpoints
- Validate LibreChat compatibility
- Test sandbox behavior and cleanup

---

## Running Tests

### Prerequisites

Before running tests, ensure:

1. **Virtual environment activated:**

   ```bash
   source .venv/bin/activate
   ```

2. **Dependencies installed:**

   ```bash
   pip install -r requirements.txt
   pip install pytest pytest-asyncio pytest-cov pytest-mock
   ```

3. **For integration tests, infrastructure running:**
   ```bash
   docker compose up -d
   ```

### Running All Tests

```bash
# Run all tests
pytest tests/

# With verbose output
pytest -v tests/

# With coverage report
pytest --cov=src tests/
```

### Running Unit Tests Only

```bash
# Run all unit tests
pytest tests/unit/

# Run a specific test file
pytest tests/unit/test_execution_service.py

# Run a specific test function
pytest tests/unit/test_execution_service.py::test_execute_python_code
```

### Running Integration Tests Only

```bash
# Run all integration tests
pytest tests/integration/

# Run core integration tests
pytest tests/integration/test_api_contracts.py \
       tests/integration/test_librechat_compat.py \
       tests/integration/test_container_behavior.py -v
```

### Running Tests by Marker

```bash
# Run only slow tests
pytest -m slow

# Skip slow tests
pytest -m "not slow"

# Run only Python-related tests
pytest -k "python"
```

---

## Key Test Files

### API Contract Tests

**File:** `tests/integration/test_api_contracts.py`

Validates API request/response formats match expectations:

- ExecRequest validation
- ExecResponse structure
- Error response formats
- HTTP status codes

### LibreChat Compatibility Tests

**File:** `tests/integration/test_librechat_compat.py`

Ensures compatibility with LibreChat's Code Interpreter API:

- File upload format (multipart/form-data)
- Session ID handling
- File reference format
- Response structure matching LibreChat expectations

### Sandbox Behavior Tests

**File:** `tests/integration/test_container_behavior.py`

Tests sandbox lifecycle and execution:

- Sandbox creation and cleanup
- Resource limit enforcement
- Timeout handling
- Output capture

### Session State Tests

**File:** `tests/integration/test_session_state.py`

Tests Python state persistence:

- Variable persistence across executions
- Function persistence
- NumPy/Pandas object persistence
- State size limits
- Session isolation

### File Handling Tests

**File:** `tests/integration/test_file_handling.py`

Tests file operations:

- File upload
- File download
- File listing
- File deletion
- File naming edge cases

---

## Writing Tests

### Using Fixtures

Common fixtures are defined in `tests/conftest.py`:

```python
import pytest

@pytest.fixture
def api_client():
    """HTTP client configured for API testing."""
    import httpx
    return httpx.AsyncClient(
        base_url="https://localhost",
        headers={"x-api-key": "test-api-key-for-development-only"},
        verify=False
    )

@pytest.fixture
def sample_python_code():
    """Sample Python code for testing."""
    return "print('Hello, World!')"
```

### Async Tests

Use `pytest.mark.asyncio` for async tests:

```python
import pytest

@pytest.mark.asyncio
async def test_execute_python(api_client):
    response = await api_client.post("/exec", json={
        "lang": "py",
        "code": "print(1+1)",
        "entity_id": "test",
        "user_id": "test"
    })
    assert response.status_code == 200
    data = response.json()
    assert data["stdout"] == "2\n"
```

### Mocking External Services

For unit tests, mock external dependencies:

```python
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_execution_with_mocked_sandbox():
    with patch("src.services.sandbox.executor.SandboxExecutor") as mock_executor:
        mock_executor.execute.return_value = ("output", "", 0)

        # Test code here
```

### Testing State Persistence

```python
@pytest.mark.asyncio
async def test_state_persistence(api_client):
    # First execution - create variable
    response1 = await api_client.post("/exec", json={
        "lang": "py",
        "code": "x = 42",
        "entity_id": "test",
        "user_id": "test"
    })
    session_id = response1.json()["session_id"]

    # Second execution - use variable
    response2 = await api_client.post("/exec", json={
        "lang": "py",
        "code": "print(x)",
        "entity_id": "test",
        "user_id": "test",
        "session_id": session_id
    })
    assert response2.json()["stdout"] == "42\n"
```

---

## Performance Testing

A dedicated performance testing script is available:

```bash
# Activate virtual environment
source .venv/bin/activate

# Install aiohttp for async HTTP requests
pip install aiohttp

# Run performance tests
python scripts/perf_test.py
```

### What Performance Tests Measure

1. **Simple execution latency** - Basic print statement
2. **Complex execution latency** - NumPy/Pandas operations
3. **Concurrent request handling** - Multiple simultaneous requests
4. **State persistence overhead** - Serialization/deserialization time
5. **File operation latency** - Upload/download speeds

### Sample Output

```
=== Performance Test Results ===

Simple Python Execution:
  Mean: 32.5ms
  P50:  28.0ms
  P99:  85.0ms

Complex Python Execution:
  Mean: 125.0ms
  P50:  110.0ms
  P99:  250.0ms

Concurrent Requests (10x):
  Mean: 45.0ms
  Max:  180.0ms
```

---

## Coverage Reports

Generate coverage reports:

```bash
# Generate HTML coverage report
pytest --cov=src --cov-report=html tests/

# View report
open htmlcov/index.html
```

### Coverage Targets

| Component       | Target | Current |
| --------------- | ------ | ------- |
| src/api/        | 90%+   | -       |
| src/services/   | 85%+   | -       |
| src/middleware/ | 80%+   | -       |
| Overall         | 80%+   | -       |

---

## CI/CD Integration

For CI/CD pipelines, use:

```bash
# Run tests with JUnit XML output
pytest --junitxml=test-results.xml tests/

# Run with coverage in CI format
pytest --cov=src --cov-report=xml tests/
```

### GitHub Actions Example

```yaml
- name: Run Tests
  run: |
    pip install -r requirements.txt
    pytest --cov=src --cov-report=xml tests/unit/

- name: Upload Coverage
  uses: codecov/codecov-action@v3
  with:
    files: ./coverage.xml
```

---

## Troubleshooting Tests

### Integration Tests Failing

1. **Check infrastructure:**

   ```bash
   docker compose ps  # All services should be "Up"
   ```

2. **Check API health:**

   ```bash
   curl -sk https://localhost/health
   ```

3. **Check logs:**
   ```bash
   docker compose logs api
   ```

### Async Test Issues

If async tests hang:

- Ensure `pytest-asyncio` is installed
- Check for unclosed async resources
- Use `@pytest.mark.asyncio` decorator

### Flaky Tests

For tests that occasionally fail:

- Check for race conditions in sandbox cleanup
- Ensure proper test isolation
- Use explicit waits for async operations

---

## Related Documentation

- [CONFIGURATION.md](CONFIGURATION.md) - Test environment configuration
- [ARCHITECTURE.md](ARCHITECTURE.md) - System architecture for test planning
- [PERFORMANCE.md](PERFORMANCE.md) - Performance testing details
