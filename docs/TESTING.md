# Testing Guide

This repository uses three test layers. Only the live HTTP layer should be treated as the source of truth for LibreChat compatibility.

## Test Layers

```text
tests/
├── unit/         # mocked component logic
├── integration/  # in-process FastAPI wiring and contract checks
├── functional/   # live HTTP coverage against a running API
└── snapshots/    # reference payloads
```

### `tests/unit/`

- Mock Redis, MinIO, sandboxing, and other infrastructure.
- Fast feedback for service logic.
- No external stack required.

### `tests/integration/`

- Exercise the FastAPI app in-process via `TestClient`.
- Validate routing, auth, request parsing, response shape, and thin wiring.
- `contract_only` tests live here.
- These tests are not end-to-end compatibility proof.

### `tests/functional/`

- Exercise a running API over real HTTP.
- Cover sessions, files, downloads, state continuity, PTC, and concurrency.
- `client_replay` tests mirror the current `librechat-agents` runtime flow.
- This layer is the primary compatibility signal for LibreChat support.

## Markers

- `contract_only`: in-process contract and wiring checks.
- `live_api`: any test that hits a running API.
- `client_replay`: runtime-faithful client replay against the live API.
- `slow`: heavier scenarios for dedicated CI jobs.

## Running Tests

### Prerequisites

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pytest pytest-asyncio pytest-cov pytest-mock
```

For live API coverage:

```bash
cp .env.example .env
docker compose up -d
```

### Fast Feedback

```bash
pytest tests/unit/ tests/integration/
```

### Full Live Compatibility Coverage

```bash
API_BASE="http://localhost:8000" \
API_KEY="your-secure-api-key-here-change-this-in-production" \
pytest tests/functional/ -m live_api -v
```

### Runtime-Faithful Client Replay Only

```bash
API_BASE="http://localhost:8000" \
API_KEY="your-secure-api-key-here-change-this-in-production" \
pytest tests/functional/ -m client_replay -v
```

### Marker-Based Runs

```bash
pytest -m contract_only
pytest -m slow
pytest -m "live_api and not slow"
```

## Current Source of Truth

- Direct `/exec` and file lifecycle compatibility: `tests/functional/test_client_replay.py`
- PTC runtime replay: `tests/functional/test_client_replay.py`
- File contracts and reuse: `tests/functional/test_files.py`
- Generated artifact integrity: `tests/functional/test_generated_artifacts.py`
- Mounted file edit persistence: `tests/functional/test_mounted_file_edits.py`

If a mocked integration test passes but a `live_api` or `client_replay` test fails, treat the live failure as authoritative.

## CI/CD Test Tiers

GitHub Actions now uses three workflow tiers:

- `ci.yml`: required PR checks for static analysis, unit tests, `contract_only` integration tests, amd64 app build validation, amd64 live smoke tests, and amd64 `client_replay`
- `release.yml`: publishes the multi-arch app image used by `docker-compose.prod.yml` after per-arch smoke validation
- `nightly.yml`: builds the app image locally and runs the full/slow live validation suites

The amd64 live smoke suite is the required compatibility gate on pull requests. Slow live scenarios stay in nightly validation so the PR path keeps the authoritative checks without forcing the heaviest runtime coverage into every change.
