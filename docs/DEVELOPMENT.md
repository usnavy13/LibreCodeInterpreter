# Development Guide

This document provides detailed instructions for setting up the development environment, installing dependencies, and running tests.

## Setup & Installation

### Prerequisites

- Python 3.11+
- Docker and docker compose (for running the API container, Redis, and Garage)
- Redis
- Garage (or S3-compatible storage)

### Installation Steps

1. **Clone the repository**

   ```bash
   git clone https://github.com/usnavy13/LibreCodeInterpreter.git
   cd LibreCodeInterpreter
   ```

2. **Create a virtual environment**

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

4. **Set up environment variables**

   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

5. **Start infrastructure services**

   ```bash
   docker compose up -d
   ```

6. **Run the API server**
   ```bash
   uvicorn src.main:app --reload
   ```

## Testing

For detailed testing instructions, please refer to [TESTING.md](TESTING.md).

### Quick Commands

```bash
# Run unit tests
pytest tests/unit/

# Run integration tests (in-process TestClient, no running stack needed)
pytest tests/integration/

# Run all tests with coverage
pytest --cov=src tests/
```

## Building the Docker Image

The repository ships a single published/deployed container image: `app`.
The `runtime-core` and `runtime-r` Docker targets remain internal build stages,
not separately published packages.

```bash
# Build the local application image
docker build --target app -t code-interpreter:nsjail .
```

By default `docker compose up -d` pulls the published image (`ghcr.io/usnavy13/librecodeinterpreter:main`). To run your locally built image instead, override `API_IMAGE`:

```bash
# Run a locally built image
API_IMAGE=code-interpreter:nsjail docker compose up -d

# Or pull a specific published tag
API_IMAGE=ghcr.io/usnavy13/librecodeinterpreter:<tag> docker compose up -d
```

For repeated local-build workflows, copy `docker-compose.override.example.yml` to `docker-compose.override.yml` and uncomment the `build:` block so `docker compose up --build -d` rebuilds from your checkout automatically.

For more details on the sandbox architecture, see [ARCHITECTURE.md](ARCHITECTURE.md).
