# syntax=docker/dockerfile:1.4
# Python execution environment with BuildKit optimizations
FROM python:3.14-slim

# Install common packages for data science and general use
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    make \
    pkg-config \
    libxml2-dev \
    libxslt-dev \
    libffi-dev \
    libcairo2-dev \
    libpango1.0-dev \
    libgdk-pixbuf-2.0-dev \
    libssl-dev \
    libjpeg-dev \
    libpng-dev \
    libtiff-dev \
    libopenjp2-7-dev \
    libfreetype6-dev \
    liblcms2-dev \
    libwebp-dev \
    tcl8.6-dev \
    tk8.6-dev \
    python3-tk \
    python3-dev \
    poppler-utils \
    tesseract-ocr \
    pandoc \
    portaudio19-dev \
    flac \
    ffmpeg \
    libpulse-dev \
    antiword \
    unrtf \
    && rm -rf /var/lib/apt/lists/*

# Configure pip and build tools
ENV PIP_NO_BUILD_ISOLATION=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install pip and build tooling with compatible versions
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install \
    "pip<24.1" \
    "setuptools<70" \
    wheel \
    "packaging<24"

# Copy requirements files
COPY requirements/python-core.txt /tmp/python-core.txt
COPY requirements/python-analysis.txt /tmp/python-analysis.txt
COPY requirements/python-visualization.txt /tmp/python-visualization.txt
COPY requirements/python-documents.txt /tmp/python-documents.txt
COPY requirements/python-utilities.txt /tmp/python-utilities.txt
COPY requirements/python-new.txt /tmp/python-new.txt

# Layer 1: Core data packages (most stable, rarely changes)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r /tmp/python-core.txt

# Layer 2: Analysis packages (math, science, ML)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r /tmp/python-analysis.txt

# Layer 3: Visualization packages
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r /tmp/python-visualization.txt

# Layer 4: Document processing packages
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r /tmp/python-documents.txt

# Layer 5: Utility packages
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r /tmp/python-utilities.txt

# Layer 6: NEW packages (changes most frequently)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r /tmp/python-new.txt

# Clean up requirements files
RUN rm -f /tmp/python-*.txt

# Create non-root user
RUN groupadd -r codeuser && useradd -r -g codeuser codeuser

# Set working directory
WORKDIR /mnt/data

# Ensure ownership of working directory
RUN chown -R codeuser:codeuser /mnt/data

# Add REPL server and entrypoint scripts
COPY repl_server.py /opt/repl_server.py
COPY entrypoint.sh /opt/entrypoint.sh
RUN chmod +x /opt/repl_server.py /opt/entrypoint.sh

# Ensure /opt is accessible
RUN chown -R codeuser:codeuser /opt

# Switch to non-root user
USER codeuser

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/mnt/data

# Use the entrypoint script which handles REPL mode
# REPL_MODE=true -> runs repl_server.py
# REPL_MODE unset/false -> runs default command
ENTRYPOINT ["/opt/entrypoint.sh"]
CMD ["tail", "-f", "/dev/null"]
