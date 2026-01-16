# syntax=docker/dockerfile:1.4
# D execution environment with BuildKit optimizations
FROM ubuntu:24.04

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

LABEL org.opencontainers.image.title="Code Interpreter D Environment" \
      org.opencontainers.image.description="Secure execution environment for D (ldc2) code" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

# Install toolchain (ldc2) and basics; works on amd64 and arm64
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl wget xz-utils git \
      build-essential make binutils \
      ldc \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user (uid:1001) consistent with other images
RUN useradd -m -u 1001 runner && mkdir -p /mnt/data && chown -R runner:runner /mnt/data

WORKDIR /mnt/data

# Switch to non-root user
USER 1001:1001

# Default command with sanitized environment
ENTRYPOINT ["/usr/bin/env","-i","PATH=/usr/local/bin:/usr/bin:/bin","HOME=/tmp","TMPDIR=/tmp"]
CMD ["ldc2", "--version"]
