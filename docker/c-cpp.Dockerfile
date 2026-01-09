# syntax=docker/dockerfile:1.4
# C/C++ execution environment with BuildKit optimizations
# Pin to specific version for reproducibility
FROM gcc:15-bookworm

# Install essential development tools and libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    make \
    cmake \
    # Math and science libraries
    libgsl-dev \
    libblas-dev \
    liblapack-dev \
    # File handling libraries
    libzip-dev \
    zlib1g-dev \
    # JSON library
    nlohmann-json3-dev \
    # CSV library
    libcsv-dev \
    # Additional utilities
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -g 1001 codeuser && \
    useradd -r -u 1001 -g codeuser codeuser

# Set working directory and ensure ownership
WORKDIR /mnt/data
RUN chown -R codeuser:codeuser /mnt/data

# Switch to non-root user
USER codeuser

# Set environment variables for C/C++ development
ENV CC=gcc \
    CXX=g++ \
    PKG_CONFIG_PATH=/usr/lib/x86_64-linux-gnu/pkgconfig

# Default command with sanitized environment
ENTRYPOINT ["/usr/bin/env","-i","PATH=/usr/local/bin:/usr/bin:/bin","HOME=/tmp","TMPDIR=/tmp","CC=gcc","CXX=g++","PKG_CONFIG_PATH=/usr/lib/x86_64-linux-gnu/pkgconfig"]
CMD ["/bin/bash"]
