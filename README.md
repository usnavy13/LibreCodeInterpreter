# LibreCodeInterpreter

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python Version](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![CI Status](https://github.com/usnavy13/LibreCodeInterpreter/actions/workflows/ci.yml/badge.svg)](https://github.com/usnavy13/LibreCodeInterpreter/actions/workflows/ci.yml)

A secure, open-source code interpreter API that provides sandboxed code execution using nsjail for isolation. Compatible with LibreChat's Code Interpreter API.

## Quick Start

Most users should run the published Docker image from GHCR. You do not need to build the application locally, and the published image supports both `amd64` and `arm64`.

1. **Clone the repository**

   ```bash
   git clone https://github.com/usnavy13/LibreCodeInterpreter.git
   cd LibreCodeInterpreter
   ```

2. **Setup environment**

   ```bash
   cp .env.example .env
   # The default settings work out-of-the-box for local usage
   ```

3. **Pull and start the stack**

   ```bash
   docker compose pull
   docker compose up -d
   ```

   By default this uses `ghcr.io/usnavy13/librecodeinterpreter:main`. To pin a different published tag:

   ```bash
   API_IMAGE=ghcr.io/usnavy13/librecodeinterpreter:<tag> docker compose up -d
   ```

4. **Verify the API**

   ```bash
   curl http://localhost:8000/health
   ```

The API will be available at `http://localhost:8000`.
Visit `http://localhost:8000/docs` for the interactive API documentation.

To enable HTTPS, set `PORT`, `ENABLE_HTTPS`, `SSL_CERTS_PATH`, `SSL_CERT_FILE`, and `SSL_KEY_FILE` in `.env`. `SSL_CERTS_PATH` is the host path mounted into the container at `/app/ssl`, while `SSL_CERT_FILE` and `SSL_KEY_FILE` must point to the certificate files inside the container. See [docs/CONFIGURATION.md](docs/CONFIGURATION.md#sslhttps-configuration).

### Common Consumer Commands

```bash
# View API logs
docker compose logs -f api

# Stop the stack
docker compose down

# Update to the latest published image
docker compose pull
docker compose up -d
```

### Published Image Channels

The project publishes two app-image channels:

- `ghcr.io/usnavy13/librecodeinterpreter`
  - stable branch tags: `main`, `latest`
  - immutable build tags: `sha-<commit>`, release tags like `v1.2.3`
- `ghcr.io/usnavy13/librecodeinterpreter-dev`
  - development branch tags: `dev`, `latest`
  - immutable build tags: `sha-<commit>`

`docker-compose.yml` is pinned to the stable package by default:

```yaml
image: ${API_IMAGE:-ghcr.io/usnavy13/librecodeinterpreter:main}
```

### Use A Local Override File

If you want to pull the current `dev` image or build from your working tree without changing tracked compose files, use a local override. Compose auto-merges `docker-compose.override.yml` on top of `docker-compose.yml`, so no extra `-f` flags are needed.

1. Copy the example override:

   ```bash
   cp docker-compose.override.example.yml docker-compose.override.yml
   ```

2. Bring the stack up:

   ```bash
   docker compose pull
   docker compose up -d
   ```

The checked-in example defaults to `ghcr.io/usnavy13/librecodeinterpreter-dev:latest`. To build from your local checkout instead, edit `docker-compose.override.yml` and switch to the commented `build:` block. In that case, skip `pull` and run:

```bash
docker compose up --build -d
```

## Build From Source

If you are developing locally or need to customize the image:

```bash
docker build --target app -t code-interpreter:nsjail .
API_IMAGE=code-interpreter:nsjail docker compose up -d
```

The Dockerfile keeps `runtime-core` and `runtime-r` as internal build stages, but only the unified `app` image is published for deployment.

## Admin Dashboard

A built-in admin dashboard is available at `http://localhost:8000/admin-dashboard` for monitoring and management:
<img width="1449" height="1256" alt="image" src="https://github.com/user-attachments/assets/7dc6eb9b-f4e8-46d7-93be-4ae1eb03f4f0" />


- **Overview**: Real-time execution metrics, success rates, and performance graphs
- **API Keys**: Create, view, and manage API keys with rate limiting
- **System Health**: Monitor Redis, S3 storage, and sandbox pool status

The dashboard requires the master API key for authentication.

## Features

- **Multi-language Support**: Execute code in 13 languages - Python, JavaScript, TypeScript, Go, Java, C, C++, PHP, Rust, R, Fortran, D, and Bash
- **Sub-50ms Python Execution**: Pre-warmed REPL sandboxes achieve ~20-40ms latency for simple Python code
- **Sandbox Pool**: Pre-warmed nsjail sandboxes provide ~3ms acquisition time (vs 500-2000ms cold start)
- **High Concurrency**: Thread-safe execution supporting 10+ concurrent requests
- **Secure Execution**: nsjail-based sandboxed environments with namespace isolation, seccomp, and resource limits
- **Programmatic Tool Calling (PTC)**: Enables AI agents to execute code that invokes external tools mid-execution via `POST /exec/programmatic`, with multi-round continuation support
- **File Management**: Upload, download, and manage files within execution sessions
- **Session Management**: Redis-based session handling with automatic cleanup
- **S3-Compatible Storage**: Garage (S3-compatible) integration for persistent file storage
- **Authentication**: API key-based authentication for secure access
- **HTTPS/SSL Support**: Optional in-container SSL/TLS termination
- **Health Monitoring**: Comprehensive health check endpoints for all dependencies
- **Metrics Collection**: Execution and API metrics for monitoring and debugging
- **Unicode Support**: Full Unicode filename support in file downloads
- **Structured Logging**: JSON-formatted logs with configurable levels and destinations
- **CORS Support**: Optional cross-origin resource sharing for web clients
- **Orphan Cleanup**: Automatic cleanup of orphaned storage objects

## Architecture

The LibreCodeInterpreter is built with a focus on security, speed, and scalability. It uses a combination of **FastAPI** for the web layer, **nsjail** for sandboxed execution, and **Redis** for session management.

Key features include:

- **Sandbox Pooling**: Pre-warmed nsjail sandboxes for sub-50ms execution.
- **Isolated Execution**: Each execution runs in its own nsjail sandbox with namespace isolation.
- **Session Persistence**: Optional state persistence for Python sessions across executions.

For a deep dive into the system design, components, and request flows, see [ARCHITECTURE.md](docs/ARCHITECTURE.md).

## API & Usage

The API provides endpoints for code execution, file management, and session state control.

- `POST /exec`: Execute code in one of the 13 supported languages.
- `POST /exec/programmatic`: Execute code with Programmatic Tool Calling (PTC) support for AI agent workflows.
- `POST /upload`: Upload files for processing.
- `GET /download`: Retrieve generated files.

Interactive documentation is available at `http://localhost:8000/docs` when the server is running.

For detailed information on all endpoints and specific language notes, see [ARCHITECTURE.md](docs/ARCHITECTURE.md#api-layer-srcapi).

## Supported Languages

We support 13 programming languages including Python, JavaScript, TypeScript, Go, Rust, Bash, and more. Each language has optimized execution paths and resource limits.

See the [Supported Languages list](docs/CONFIGURATION.md#supported-languages) for details on versions and included libraries.

## Configuration

The service is highly configurable via environment variables.

| Category      | Description                                 |
| ------------- | ------------------------------------------- |
| **API**       | Host, port, and security settings.          |
| **Storage**   | Redis and S3 (Garage / MinIO / AWS) connection details. |
| **Resources** | Per-execution memory, CPU, and time limits. |
| **Pools**     | Sandbox pool sizing and warmup settings.    |

A full list of configuration options and a production checklist can be found in [CONFIGURATION.md](docs/CONFIGURATION.md).

## Development & Installation

For detailed instructions on setting up your local environment, running tests, and building custom images, please refer to the [Development Guide](docs/DEVELOPMENT.md).

Quick test command:

```bash
pytest tests/unit/
```

For comprehensive testing details, see [TESTING.md](docs/TESTING.md).

## CI/CD

GitHub Actions is split into two workflows:

- `ci.yml`: PR validation — static analysis (flake8, black, mypy, bandit), unit tests, and integration tests
- `release.yml`: publishes multi-arch app images for `main`, `dev`, and release tags

Published images use native `amd64` and `arm64` builds and are exposed as separate stable and dev GHCR packages.

## Security

- All code execution happens in nsjail sandboxes with namespace isolation
- PID, mount, and network namespaces isolate each execution
- Seccomp syscall filtering restricts available system calls
- Cgroup-based resource limits prevent CPU, memory, and process exhaustion
- rlimits restrict file sizes, open file descriptors, etc.
- Code runs as a shared non-root sandbox user (default uid `1001`, configurable with `SANDBOX_UID`)
- Read-only bind mounts for language runtimes and libraries
- API key authentication protects all endpoints
- Input validation prevents injection attacks

Please see [SECURITY.md](docs/SECURITY.md) for our security policy and reporting instructions.

## Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for details on how to get started, our code of conduct, and the pull request process.

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.
