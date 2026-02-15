# LibreCodeInterpreter

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python Version](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![CI Status](https://github.com/usnavy13/LibreCodeInterpreter/actions/workflows/lint.yml/badge.svg)](https://github.com/usnavy13/LibreCodeInterpreter/actions/workflows/lint.yml)

A secure, open-source code interpreter API that provides sandboxed code execution using nsjail for isolation. Compatible with LibreChat's Code Interpreter API.

## Quick Start

Get up and running in minutes by building the execution environment.

1. **Clone the repository**

   ```bash
   git clone https://github.com/usnavy13/LibreCodeInterpreter.git
   cd LibreCodeInterpreter
   ```

2. **Setup environment**

   ```bash
   cp .env.example .env
   # The default settings work out-of-the-box for local development
   ```

3. **Build the unified Docker image**

   ```bash
   docker build -t code-interpreter:nsjail .
   ```

   This builds a single image containing all 12 language runtimes and nsjail for sandboxed execution.

4. **Start the API**

   ```bash
   docker compose up -d
   ```

The API will be available at `http://localhost:8000`.
Visit `http://localhost:8000/docs` for the interactive API documentation.

## Admin Dashboard

A built-in admin dashboard is available at `http://localhost:8000/admin-dashboard` for monitoring and management:
<img width="1449" height="1256" alt="image" src="https://github.com/user-attachments/assets/7dc6eb9b-f4e8-46d7-93be-4ae1eb03f4f0" />


- **Overview**: Real-time execution metrics, success rates, and performance graphs
- **API Keys**: Create, view, and manage API keys with rate limiting
- **System Health**: Monitor Redis, MinIO, and sandbox pool status

The dashboard requires the master API key for authentication.

## Features

- **Multi-language Support**: Execute code in 12 languages - Python, JavaScript, TypeScript, Go, Java, C, C++, PHP, Rust, R, Fortran, and D
- **Sub-50ms Python Execution**: Pre-warmed REPL sandboxes achieve ~20-40ms latency for simple Python code
- **Sandbox Pool**: Pre-warmed nsjail sandboxes provide ~3ms acquisition time (vs 500-2000ms cold start)
- **High Concurrency**: Thread-safe execution supporting 10+ concurrent requests
- **Secure Execution**: nsjail-based sandboxed environments with namespace isolation, seccomp, and resource limits
- **File Management**: Upload, download, and manage files within execution sessions
- **Session Management**: Redis-based session handling with automatic cleanup
- **S3-Compatible Storage**: MinIO integration for persistent file storage
- **Authentication**: API key-based authentication for secure access
- **HTTPS/SSL Support**: Optional SSL/TLS encryption with automatic HTTP to HTTPS redirection
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
- **Stateless Execution**: Each execution is isolated and ephemeral.
- **Session Persistence**: Optional state persistence for Python sessions.

For a deep dive into the system design, components, and request flows, see [ARCHITECTURE.md](docs/ARCHITECTURE.md).

## API & Usage

The API provides endpoints for code execution, file management, and session state control.

- `POST /exec`: Execute code in one of the 12 supported languages.
- `POST /upload`: Upload files for processing.
- `GET /download`: Retrieve generated files.

Interactive documentation is available at `http://localhost:8000/docs` when the server is running.

For detailed information on all endpoints and specific language notes, see [ARCHITECTURE.md](docs/ARCHITECTURE.md#api-layer-srcapi).

## Supported Languages

We support 12 programming languages including Python, JavaScript, TypeScript, Go, Rust, and more. Each language has optimized execution paths and resource limits.

See the [Supported Languages table](docs/ARCHITECTURE.md#supported-languages) for details on versions and included libraries.

## Configuration

The service is highly configurable via environment variables.

| Category      | Description                                 |
| ------------- | ------------------------------------------- |
| **API**       | Host, port, and security settings.          |
| **Storage**   | Redis and MinIO/S3 connection details.      |
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

## Security

- All code execution happens in nsjail sandboxes with namespace isolation
- PID, mount, and network namespaces isolate each execution
- Seccomp syscall filtering restricts available system calls
- Cgroup-based resource limits prevent CPU, memory, and process exhaustion
- rlimits restrict file sizes, open file descriptors, etc.
- Code runs as non-root user (uid 1001)
- Read-only bind mounts for language runtimes and libraries
- API key authentication protects all endpoints
- Input validation prevents injection attacks

Please see [SECURITY.md](docs/SECURITY.md) for our security policy and reporting instructions.

## Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for details on how to get started, our code of conduct, and the pull request process.

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.
