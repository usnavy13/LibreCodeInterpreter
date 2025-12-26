# syntax=docker/dockerfile:1.4
# Rust execution environment with BuildKit optimizations
FROM rust:1.92-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    pkg-config \
    libssl-dev \
    libfontconfig1-dev \
    libfreetype6-dev \
    && rm -rf /var/lib/apt/lists/*

# Create a temporary project to pre-compile and cache crates
WORKDIR /tmp/rust-cache

# Copy Cargo.toml for crate caching
COPY requirements/rust-Cargo.toml Cargo.toml

# Create minimal src/main.rs (cargo init would fail since Cargo.toml exists)
RUN mkdir -p src && echo 'fn main() {}' > src/main.rs

# Pre-compile crates with cache mounts
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/tmp/rust-cache/target \
    cargo build --release || true

# Clean up the temporary project but keep the cargo cache
WORKDIR /
RUN rm -rf /tmp/rust-cache

# Create non-root user
RUN groupadd -g 1001 codeuser && \
    useradd -r -u 1001 -g codeuser codeuser

# Set working directory and ensure ownership
WORKDIR /mnt/data
RUN chown -R codeuser:codeuser /mnt/data

# Switch to non-root user
USER codeuser

# Set environment variables
ENV CARGO_HOME=/usr/local/cargo \
    RUSTUP_HOME=/usr/local/rustup \
    PATH=/usr/local/cargo/bin:$PATH

# Default command with sanitized environment
ENTRYPOINT ["/usr/bin/env","-i","PATH=/usr/local/cargo/bin:/usr/local/bin:/usr/bin:/bin","HOME=/tmp","TMPDIR=/tmp","CARGO_HOME=/usr/local/cargo","RUSTUP_HOME=/usr/local/rustup"]
CMD ["rustc", "--version"]
