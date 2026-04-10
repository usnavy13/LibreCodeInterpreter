# Development Guide

This document provides detailed instructions for setting up the development environment, installing dependencies, and running tests.

## Setup & Installation

### Prerequisites

- Python 3.11+
- Docker and docker compose (for running the API container, Redis, and MinIO)
- Redis
- MinIO (or S3-compatible storage)

### Installation Steps

1. **Clone the repository**

   ```bash
   git clone https://github.com/LibreCodeInterpreter/LibreCodeInterpreter.git
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

# Run integration tests (requires running API container, Redis, MinIO)
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

`docker compose up -d` continues to work for local development. Consumers should prefer `docker-compose.prod.yml`, which contains no `build:` step and always pulls the published image.

```bash
API_IMAGE=code-interpreter:nsjail docker compose up -d

# Consumer path using the published GHCR image
docker compose -f docker-compose.prod.yml up -d
```

For more details on the sandbox architecture, see [ARCHITECTURE.md](ARCHITECTURE.md).
