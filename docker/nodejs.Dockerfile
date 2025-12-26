# syntax=docker/dockerfile:1.4
# Node.js execution environment with BuildKit optimizations
FROM node:25-alpine

# Install common build tools
RUN apk add --no-cache \
    python3 \
    make \
    g++ \
    git

# Copy package list
COPY requirements/nodejs.txt /tmp/nodejs.txt

# Install packages with cache mount
# Read packages from file and install globally
RUN --mount=type=cache,target=/root/.npm \
    cat /tmp/nodejs.txt | grep -v '^#' | grep -v '^$' | xargs npm install -g

# Clean up
RUN rm -f /tmp/nodejs.txt

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
ENV NODE_ENV=sandbox \
    NODE_PATH=/usr/local/lib/node_modules

# Default command with sanitized environment
ENTRYPOINT ["/usr/bin/env","-i","PATH=/usr/local/bin:/usr/bin:/bin","HOME=/tmp","TMPDIR=/tmp","NODE_PATH=/usr/local/lib/node_modules"]
CMD ["node"]
