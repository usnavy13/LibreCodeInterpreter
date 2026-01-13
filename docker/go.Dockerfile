# syntax=docker/dockerfile:1.4
# Go execution environment with BuildKit optimizations
FROM golang:1.25-alpine

# Install common tools
RUN apk add --no-cache \
    git \
    make \
    gcc \
    musl-dev

# Copy go.mod for pre-downloading
COPY requirements/go.mod /tmp/gosetup/go.mod

# Pre-download common Go packages with cache mount
RUN --mount=type=cache,target=/go/pkg/mod \
    cd /tmp/gosetup && \
    go mod download && \
    cd / && rm -rf /tmp/gosetup

# Create non-root user
RUN addgroup -g 1001 -S codeuser && \
    adduser -S codeuser -u 1001 -G codeuser

# Set working directory
WORKDIR /mnt/data

# Ensure ownership of working directory
RUN chown -R codeuser:codeuser /mnt/data

# Switch to non-root user
USER codeuser

# Set environment variables
ENV GO111MODULE=on \
    GOPROXY=https://proxy.golang.org,direct \
    GOSUMDB=sum.golang.org

# Default command with sanitized environment
ENTRYPOINT ["/usr/bin/env","-i","PATH=/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin","HOME=/tmp","TMPDIR=/tmp","GO111MODULE=on","GOPROXY=https://proxy.golang.org,direct","GOSUMDB=sum.golang.org","GOCACHE=/tmp/go-build"]
CMD ["go"]
