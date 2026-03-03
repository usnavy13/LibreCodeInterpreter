# Changelog

All notable changes to LibreCodeInterpreter will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.2.0] - 2026-03-03

### Added
- Programmatic Tool Calling (PTC) API for structured tool interactions between LLMs and sandboxed code
- Bash language support (13 languages now supported)
- nsjail-based sandboxing for code execution (replaces Docker socket-based approach)
- Single unified Docker image with all 13 language runtimes
- State size threshold for hybrid Redis/MinIO storage (large states go directly to MinIO)
- Session-level file reference tracking and user isolation
- Hour and day periods for execution heatmap visualizations
- MyPy type checking integration with comprehensive type hints
- Dynamic Content Security Policy headers based on request path
- SDL2 development libraries for Python graphical workloads

### Changed
- Migrated from per-language Docker containers to nsjail sandboxes for isolation
- Replaced ContainerPool/Manager/Executor with SandboxPool/Manager/Executor
- Simplified Docker setup: single Dockerfile and docker-compose.yml
- Enhanced session management with file reference tracking and user isolation
- Updated API port configuration and removed HTTPS port references
- Improved heatmap UI styling for better visualization
- Updated Pydantic settings configuration for better type safety
- Bumped 5 dependencies to latest minor/patch versions

### Fixed
- REPL file detection for pandas export methods (to_csv, to_excel, etc.)

### Removed
- Per-language Docker images and build-images.sh script
- Docker SDK dependency (no Docker socket needed)
- docker-compose.ghcr.yml (single compose file now)
- Container-based execution service (replaced by sandbox service)
- SQLite metrics storage (replaced by in-memory metrics)
- State API endpoints (consolidated into session management)
- Deprecated baseline performance documentation files
- Legacy deployment and WAN network scripts

## [0.1.0] - 2025-12-26

### Added

#### Core Features
- Multi-language code execution supporting 12 languages: Python, JavaScript, TypeScript, Go, Java, C, C++, PHP, Rust, R, Fortran, and D
- FastAPI-based REST API with interactive documentation
- Sandboxed execution environments with comprehensive security controls
- Redis-based session management with automatic cleanup
- MinIO/S3-compatible storage integration for persistent file storage

#### Performance Features
- REPL mode for Python with pre-warmed interpreter achieving 20-40ms execution latency
- Sandbox pooling system with pre-warmed sandboxes for ~3ms acquisition time
- Thread-safe execution supporting 10+ concurrent requests
- State persistence for Python sessions with Redis and MinIO archival

#### Security Features
- API key-based authentication with rate limiting
- nsjail-based sandbox isolation with PID, mount, and network namespaces
- Seccomp syscall filtering
- Cgroup-based resource limits for CPU, memory, and PID count
- Non-root code execution (uid 1001)
- Resource limits for CPU, memory, and execution time
- Input validation and sanitization

#### File Management
- File upload and download with session isolation
- Unicode filename support
- Automatic cleanup of orphaned storage objects
- File listing and deletion per session

#### Monitoring & Observability
- Admin dashboard API for system analytics and monitoring
- Execution metrics (latency, success rates, language usage)
- API metrics (request counts, error rates, endpoint usage)
- Comprehensive health check endpoints for Redis, MinIO, and sandbox health
- Detailed health status reporting
- Structured JSON logging with configurable levels
- Request/response logging middleware

#### API Key Management
- CLI tool for API key creation, listing, and deletion
- Rate limit configuration per API key
- Web UI for API key management
- Key rotation and lifecycle management

#### Testing & Quality
- Comprehensive load testing framework with multiple scenarios
- Integration test suite with container lifecycle testing
- Metrics collection and reporting for load tests
- CI/CD workflows for automated testing and building
- Docker image publishing workflow with multi-architecture support

#### Documentation
- Comprehensive architecture documentation
- Configuration guide with all environment variables
- Development setup instructions
- Testing guide with coverage reporting
- State persistence implementation details
- REPL mode documentation
- Performance optimization guide
- Security best practices
- API key management guide
- Metrics collection documentation

#### Developer Experience
- CORS configuration for web clients
- Environment-specific configuration with .env support
- Hot reload in development mode

### Changed
- Refactored execution service for better modularity
- Enhanced R execution environment with additional graphics libraries
- Improved state archival with better logging and error handling
- Consolidated documentation from README to dedicated files

### Fixed
- Container naming convention for better tracking
- Execution time limit handling
- State persistence edge cases

### Security
- Implemented comprehensive container hardening
- Added input validation across all endpoints
- Enforced resource limits to prevent DoS attacks
- Secured file upload/download with session validation

[unreleased]: https://github.com/usnavy13/LibreCodeInterpreter/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/usnavy13/LibreCodeInterpreter/compare/v0.1.0...v1.2.0
[0.1.0]: https://github.com/usnavy13/LibreCodeInterpreter/releases/tag/v0.1.0
