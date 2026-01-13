# Multi-language execution environment for Azure Container Apps
# Note: BuildKit features removed for ACR cloud build compatibility
# Combines all 12 language runtimes into a single image for warm pool deployment

FROM ubuntu:22.04 AS base

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# =============================================================================
# STAGE 1: System dependencies and core tools
# =============================================================================

# Install base system dependencies needed by multiple languages
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Core utilities
    curl \
    wget \
    ca-certificates \
    gnupg \
    git \
    xz-utils \
    unzip \
    # Build tools
    build-essential \
    make \
    cmake \
    pkg-config \
    # Common libraries
    libssl-dev \
    libcurl4-openssl-dev \
    libxml2-dev \
    libffi-dev \
    zlib1g-dev \
    # Sandboxing tool
    bubblewrap \
    && rm -rf /var/lib/apt/lists/*

# =============================================================================
# STAGE 2: Python 3.13 with data science libraries
# =============================================================================

# Add deadsnakes PPA for Python 3.13
RUN apt-get update && apt-get install -y --no-install-recommends software-properties-common \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        python3.13 \
        python3.13-venv \
        python3.13-dev \
    && rm -rf /var/lib/apt/lists/*

# Set Python 3.13 as default
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.13 1 \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.13 1

# Install pip using get-pip.py (avoids distutils issue with Python 3.13)
RUN curl -sS https://bootstrap.pypa.io/get-pip.py | python3

# Install Python system dependencies for data science packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Image processing
    libcairo2-dev \
    libpango1.0-dev \
    libgdk-pixbuf-2.0-dev \
    libjpeg-dev \
    libpng-dev \
    libtiff-dev \
    libopenjp2-7-dev \
    libfreetype6-dev \
    liblcms2-dev \
    libwebp-dev \
    # Tkinter
    tcl8.6-dev \
    tk8.6-dev \
    python3-tk \
    # Document processing
    poppler-utils \
    tesseract-ocr \
    pandoc \
    antiword \
    unrtf \
    # Audio/Video
    portaudio19-dev \
    flac \
    ffmpeg \
    libpulse-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy Python requirements files (build from project root)
COPY docker/requirements/python-core.txt /tmp/python-core.txt
COPY docker/requirements/python-analysis.txt /tmp/python-analysis.txt
COPY docker/requirements/python-visualization.txt /tmp/python-visualization.txt
COPY docker/requirements/python-documents.txt /tmp/python-documents.txt
COPY docker/requirements/python-utilities.txt /tmp/python-utilities.txt
COPY docker/requirements/python-new.txt /tmp/python-new.txt

# Install build tools (pip already installed via get-pip.py)
ENV PIP_NO_BUILD_ISOLATION=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install \
    "setuptools<70" \
    wheel \
    "packaging<24"

# Install Python packages in layers
RUN pip install --no-cache-dir -r /tmp/python-core.txt

RUN pip install --no-cache-dir -r /tmp/python-analysis.txt

RUN pip install --no-cache-dir -r /tmp/python-visualization.txt

RUN pip install --no-cache-dir -r /tmp/python-documents.txt

RUN pip install --no-cache-dir -r /tmp/python-utilities.txt

RUN pip install --no-cache-dir -r /tmp/python-new.txt

# Install FastAPI and uvicorn for the executor service
RUN pip install \
    fastapi \
    uvicorn[standard] \
    httpx \
    pydantic \
    python-multipart

# Clean up Python requirements
RUN rm -f /tmp/python-*.txt

# =============================================================================
# STAGE 3: Node.js 20 LTS
# =============================================================================

RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Node.js packages
COPY docker/requirements/nodejs.txt /tmp/nodejs.txt
RUN cat /tmp/nodejs.txt | grep -v '^#' | grep -v '^$' | xargs npm install -g \
    && rm -f /tmp/nodejs.txt

# Install TypeScript globally
RUN npm install -g typescript ts-node

# =============================================================================
# STAGE 4: Go 1.23
# =============================================================================

ENV GO_VERSION=1.23.4
RUN curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz" | tar -C /usr/local -xzf -

ENV PATH="/usr/local/go/bin:${PATH}" \
    GOPATH="/go" \
    GO111MODULE=on \
    GOPROXY=https://proxy.golang.org,direct

# Pre-download common Go packages
COPY docker/requirements/go.mod /tmp/gosetup/go.mod
RUN cd /tmp/gosetup && go mod download \
    && rm -rf /tmp/gosetup

# =============================================================================
# STAGE 5: Java (Eclipse Temurin 21)
# =============================================================================

RUN mkdir -p /etc/apt/keyrings \
    && wget -O - https://packages.adoptium.net/artifactory/api/gpg/key/public | tee /etc/apt/keyrings/adoptium.asc \
    && echo "deb [signed-by=/etc/apt/keyrings/adoptium.asc] https://packages.adoptium.net/artifactory/deb $(cat /etc/lsb-release | grep DISTRIB_CODENAME | cut -d= -f2) main" | tee /etc/apt/sources.list.d/adoptium.list \
    && apt-get update \
    && apt-get install -y temurin-21-jdk \
    && rm -rf /var/lib/apt/lists/*

# Download Java libraries
RUN mkdir -p /opt/java/lib && cd /opt/java/lib && \
    wget -q https://repo1.maven.org/maven2/org/apache/commons/commons-csv/1.10.0/commons-csv-1.10.0.jar && \
    wget -q https://repo1.maven.org/maven2/org/apache/commons/commons-lang3/3.14.0/commons-lang3-3.14.0.jar && \
    wget -q https://repo1.maven.org/maven2/org/apache/commons/commons-math3/3.6.1/commons-math3-3.6.1.jar && \
    wget -q https://repo1.maven.org/maven2/com/fasterxml/jackson/core/jackson-core/2.16.0/jackson-core-2.16.0.jar && \
    wget -q https://repo1.maven.org/maven2/com/fasterxml/jackson/core/jackson-databind/2.16.0/jackson-databind-2.16.0.jar && \
    wget -q https://repo1.maven.org/maven2/com/fasterxml/jackson/core/jackson-annotations/2.16.0/jackson-annotations-2.16.0.jar && \
    wget -q https://repo1.maven.org/maven2/com/google/guava/guava/33.0.0-jre/guava-33.0.0-jre.jar && \
    wget -q https://repo1.maven.org/maven2/com/google/code/gson/gson/2.10.1/gson-2.10.1.jar

ENV JAVA_HOME=/usr/lib/jvm/temurin-21-jdk-amd64 \
    CLASSPATH="/mnt/data:/opt/java/lib/*"

# =============================================================================
# STAGE 6: C/C++ (GCC)
# =============================================================================

# GCC is already installed via build-essential
# Install additional libraries for C/C++
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgsl-dev \
    libblas-dev \
    liblapack-dev \
    libzip-dev \
    nlohmann-json3-dev \
    libcsv-dev \
    && rm -rf /var/lib/apt/lists/*

# =============================================================================
# STAGE 7: PHP 8.2
# =============================================================================

RUN apt-get update && apt-get install -y --no-install-recommends \
    php8.1-cli \
    php8.1-xml \
    php8.1-zip \
    php8.1-gd \
    php8.1-mbstring \
    php8.1-curl \
    && rm -rf /var/lib/apt/lists/*

# Install Composer
RUN curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer

# Create composer home and install packages
RUN mkdir -p /opt/composer/global
ENV COMPOSER_HOME=/opt/composer/global

RUN composer global require \
    league/csv \
    ramsey/uuid \
    nesbot/carbon \
    guzzlehttp/guzzle \
    symfony/yaml \
    --optimize-autoloader || true

ENV PATH="/opt/composer/global/vendor/bin:${PATH}"

# =============================================================================
# STAGE 8: Rust
# =============================================================================

ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable --profile minimal

ENV PATH="/usr/local/cargo/bin:${PATH}"

# Pre-compile common crates
COPY docker/requirements/rust-Cargo.toml /tmp/rust-cache/Cargo.toml
RUN mkdir -p /tmp/rust-cache/src && echo 'fn main() {}' > /tmp/rust-cache/src/main.rs \
    && cd /tmp/rust-cache \
    && cargo build --release || true \
    && rm -rf /tmp/rust-cache

# =============================================================================
# STAGE 9: R
# =============================================================================

RUN apt-get update && apt-get install -y --no-install-recommends \
    r-base \
    r-base-dev \
    libfontconfig1-dev \
    libharfbuzz-dev \
    libfribidi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install common R packages
RUN R -e "options(repos = c(CRAN = 'https://cloud.r-project.org')); \
    install.packages(c('dplyr', 'tidyr', 'data.table', 'ggplot2', 'jsonlite', 'stringr'))" || true

# =============================================================================
# STAGE 10: Fortran
# =============================================================================

RUN apt-get update && apt-get install -y --no-install-recommends \
    gfortran \
    libnetcdf-dev \
    libhdf5-dev \
    && rm -rf /var/lib/apt/lists/*

ENV FC=gfortran \
    F77=gfortran \
    F90=gfortran

# =============================================================================
# STAGE 11: D Language (LDC2)
# =============================================================================

RUN apt-get update && apt-get install -y --no-install-recommends \
    ldc \
    && rm -rf /var/lib/apt/lists/*

# =============================================================================
# STAGE 12: Final setup
# =============================================================================

# Create non-root user for code execution
RUN groupadd -g 1001 codeuser && \
    useradd -r -u 1001 -g codeuser codeuser

# Create directories
RUN mkdir -p /mnt/data /app /opt/executor \
    && chown -R codeuser:codeuser /mnt/data

# Set working directory
WORKDIR /app

# Copy executor service
COPY src/executor /opt/executor

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt \
    NODE_ENV=sandbox \
    NODE_PATH=/usr/local/lib/node_modules \
    GO111MODULE=on \
    GOPROXY=https://proxy.golang.org,direct \
    EXECUTOR_PORT=8001 \
    MAX_CONCURRENT_EXECUTIONS=4 \
    WORKING_DIR_BASE=/mnt/data

# Expose executor port
EXPOSE 8001

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

# Default command: run the executor service with uvicorn
CMD ["python3", "-m", "uvicorn", "executor.main:app", "--host", "0.0.0.0", "--port", "8001"]
