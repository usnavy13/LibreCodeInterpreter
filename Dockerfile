# syntax=docker/dockerfile:1.7
# Unified multi-stage Dockerfile:
# - runtime-core: internal polyglot build stage without R
# - runtime-r: internal heavyweight R build stage
# - app: published API/application image

ARG UBUNTU_VERSION=24.04
ARG GO_VERSION=1.23.6
ARG NSJAIL_REF=b7ff9f30188a7845d41366e1e3b3929f464ac443

FROM ubuntu:${UBUNTU_VERSION} AS runtime-core

ARG DEBIAN_FRONTEND=noninteractive
ARG GO_VERSION
ARG NSJAIL_REF

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# ============================================
# System dependencies + nsjail
# ============================================
RUN apt-get update && apt-get install -y --no-install-recommends \
    git cmake build-essential pkg-config \
    libprotobuf-dev protobuf-compiler \
    libnl-3-dev libnl-route-3-dev \
    flex bison \
    curl wget ca-certificates gnupg software-properties-common \
    libssl-dev libffi-dev libxml2-dev libxslt-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/google/nsjail.git /tmp/nsjail && \
    cd /tmp/nsjail && \
    git checkout "${NSJAIL_REF}" && \
    make -j"$(nproc)" && \
    cp /tmp/nsjail/nsjail /usr/local/bin/nsjail && \
    chmod +x /usr/local/bin/nsjail && \
    rm -rf /tmp/nsjail

# ============================================
# Python 3.12 (primary runtime)
# ============================================
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-dev \
    python3-tk \
    gcc g++ make pkg-config \
    libcairo2-dev libpango1.0-dev libgdk-pixbuf-2.0-dev \
    libjpeg-dev libpng-dev libtiff-dev libopenjp2-7-dev \
    libfreetype6-dev liblcms2-dev libwebp-dev \
    tcl8.6-dev tk8.6-dev \
    poppler-utils tesseract-ocr pandoc \
    portaudio19-dev flac ffmpeg \
    libpulse-dev libsdl2-dev libsdl2-mixer-dev libsdl2-image-dev libsdl2-ttf-dev \
    antiword unrtf \
    && rm -rf /var/lib/apt/lists/*

COPY docker/requirements/python-core.txt /tmp/python-core.txt
COPY docker/requirements/python-analysis.txt /tmp/python-analysis.txt
COPY docker/requirements/python-visualization.txt /tmp/python-visualization.txt
COPY docker/requirements/python-documents.txt /tmp/python-documents.txt
COPY docker/requirements/python-utilities.txt /tmp/python-utilities.txt
COPY docker/requirements/python-new.txt /tmp/python-new.txt

RUN --mount=type=cache,target=/root/.cache/pip \
    python3 -m pip install --break-system-packages --ignore-installed \
    "pip<24.1" "setuptools<70" wheel "packaging<24"

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --break-system-packages -r /tmp/python-core.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --break-system-packages -r /tmp/python-analysis.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --break-system-packages -r /tmp/python-visualization.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --break-system-packages -r /tmp/python-documents.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --break-system-packages -r /tmp/python-utilities.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --break-system-packages -r /tmp/python-new.txt

RUN rm -f /tmp/python-*.txt

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/mnt/data

# ============================================
# Node.js (for JavaScript / TypeScript)
# ============================================
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

COPY docker/requirements/nodejs.txt /tmp/nodejs.txt
RUN --mount=type=cache,target=/root/.npm \
    grep -v '^#' /tmp/nodejs.txt | grep -v '^$' | xargs npm install -g && \
    rm -f /tmp/nodejs.txt

ENV NODE_ENV=sandbox \
    NODE_PATH=/usr/local/lib/node_modules

# ============================================
# Go
# ============================================
RUN curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-$(dpkg --print-architecture).tar.gz" \
    | tar -C /usr/local -xzf -

ENV PATH="/usr/local/go/bin:${PATH}" \
    GOPATH="/usr/local/gopath" \
    GO111MODULE=on \
    GOPROXY=https://proxy.golang.org,direct \
    GOSUMDB=sum.golang.org

COPY docker/requirements/go.mod /tmp/gosetup/go.mod
RUN --mount=type=cache,target=/usr/local/gopath/pkg/mod \
    cd /tmp/gosetup && go mod download && \
    cd / && rm -rf /tmp/gosetup

# ============================================
# Java (JDK)
# ============================================
RUN apt-get update && apt-get install -y --no-install-recommends \
    default-jdk \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/java/lib && cd /opt/java/lib && \
    wget -q https://repo1.maven.org/maven2/org/apache/commons/commons-csv/1.10.0/commons-csv-1.10.0.jar && \
    wget -q https://repo1.maven.org/maven2/org/apache/commons/commons-lang3/3.14.0/commons-lang3-3.14.0.jar && \
    wget -q https://repo1.maven.org/maven2/org/apache/commons/commons-math3/3.6.1/commons-math3-3.6.1.jar && \
    wget -q https://repo1.maven.org/maven2/org/apache/commons/commons-collections4/4.4/commons-collections4-4.4.jar && \
    wget -q https://repo1.maven.org/maven2/org/apache/commons/commons-compress/1.25.0/commons-compress-1.25.0.jar && \
    wget -q https://repo1.maven.org/maven2/org/apache/commons/commons-text/1.11.0/commons-text-1.11.0.jar && \
    wget -q https://repo1.maven.org/maven2/com/fasterxml/jackson/core/jackson-core/2.16.0/jackson-core-2.16.0.jar && \
    wget -q https://repo1.maven.org/maven2/com/fasterxml/jackson/core/jackson-databind/2.16.0/jackson-databind-2.16.0.jar && \
    wget -q https://repo1.maven.org/maven2/com/fasterxml/jackson/core/jackson-annotations/2.16.0/jackson-annotations-2.16.0.jar && \
    wget -q https://repo1.maven.org/maven2/org/apache/poi/poi/5.2.5/poi-5.2.5.jar && \
    wget -q https://repo1.maven.org/maven2/org/apache/poi/poi-ooxml/5.2.5/poi-ooxml-5.2.5.jar && \
    wget -q https://repo1.maven.org/maven2/org/apache/poi/poi-ooxml-lite/5.2.5/poi-ooxml-lite-5.2.5.jar && \
    wget -q https://repo1.maven.org/maven2/org/apache/xmlbeans/xmlbeans/5.2.0/xmlbeans-5.2.0.jar && \
    wget -q https://repo1.maven.org/maven2/org/apache/pdfbox/pdfbox/3.0.1/pdfbox-3.0.1.jar && \
    wget -q https://repo1.maven.org/maven2/org/apache/pdfbox/fontbox/3.0.1/fontbox-3.0.1.jar && \
    wget -q https://repo1.maven.org/maven2/com/google/guava/guava/33.0.0-jre/guava-33.0.0-jre.jar && \
    wget -q https://repo1.maven.org/maven2/com/google/code/gson/gson/2.10.1/gson-2.10.1.jar && \
    wget -q https://repo1.maven.org/maven2/joda-time/joda-time/2.12.5/joda-time-2.12.5.jar

ENV JAVA_OPTS="-Xmx512m -Xms128m" \
    CLASSPATH="/mnt/data:/opt/java/lib/*"

# ============================================
# C/C++ (GCC)
# ============================================
RUN apt-get update && apt-get install -y --no-install-recommends \
    cmake \
    libgsl-dev libblas-dev liblapack-dev \
    libzip-dev \
    nlohmann-json3-dev \
    libcsv-dev \
    && rm -rf /var/lib/apt/lists/*

ENV CC=gcc \
    CXX=g++

# ============================================
# PHP
# ============================================
RUN apt-get update && apt-get install -y --no-install-recommends \
    php php-cli php-common \
    php-xml php-zip php-gd php-mbstring \
    php-curl php-json \
    libonig-dev unzip \
    && rm -rf /var/lib/apt/lists/*

RUN curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer

ENV COMPOSER_HOME=/opt/composer/global
RUN mkdir -p /opt/composer/global && \
    composer global require \
    league/csv \
    phpoffice/phpspreadsheet \
    league/flysystem \
    intervention/image \
    ramsey/uuid \
    nesbot/carbon \
    markrogoyski/math-php \
    guzzlehttp/guzzle \
    symfony/yaml \
    symfony/console \
    --optimize-autoloader

ENV PHP_INI_SCAN_DIR="/etc/php/8.3/cli/conf.d"

# ============================================
# Rust
# ============================================
ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | \
    sh -s -- -y --default-toolchain stable --profile minimal && \
    chmod -R a+r /usr/local/cargo /usr/local/rustup

ENV PATH="/usr/local/cargo/bin:${PATH}"

COPY docker/requirements/rust-Cargo.toml /tmp/rust-cache/Cargo.toml
RUN mkdir -p /tmp/rust-cache/src && echo 'fn main() {}' > /tmp/rust-cache/src/main.rs && \
    cd /tmp/rust-cache && cargo build --release || true && \
    rm -rf /tmp/rust-cache

# ============================================
# Fortran (gfortran)
# ============================================
RUN apt-get update && apt-get install -y --no-install-recommends \
    gfortran \
    libnetcdf-dev libhdf5-dev \
    && rm -rf /var/lib/apt/lists/*

ENV FORTRAN_COMPILER=gfortran \
    FC=gfortran \
    F77=gfortran \
    F90=gfortran \
    F95=gfortran

# ============================================
# D Language (LDC)
# ============================================
RUN apt-get update && apt-get install -y --no-install-recommends \
    ldc \
    binutils \
    && rm -rf /var/lib/apt/lists/*

# ============================================
# Document processing: LibreOffice, qpdf, fonts
# ============================================
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer libreoffice-calc libreoffice-core libreoffice-common \
    fonts-liberation fonts-dejavu-core fonts-noto-core \
    qpdf \
    && rm -rf /var/lib/apt/lists/*

# ============================================
# Sandbox directory structure
# ============================================
RUN mkdir -p /var/lib/code-interpreter/sandboxes && \
    mkdir -p /mnt/data && \
    mkdir -p /tmp/empty_proc

RUN groupadd -g 1001 codeuser && \
    useradd -u 1001 -g codeuser -m codeuser && \
    chown -R codeuser:codeuser /mnt/data

ENV PATH="/usr/local/cargo/bin:/usr/local/go/bin:/opt/composer/global/vendor/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH}" \
    SANDBOX_BASE_DIR=/var/lib/code-interpreter/sandboxes

FROM runtime-core AS runtime-r

ARG DEBIAN_FRONTEND=noninteractive

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# ============================================
# R
# ============================================
RUN apt-get update && apt-get install -y --no-install-recommends \
    r-base r-base-dev \
    libcurl4-openssl-dev \
    libfontconfig1-dev libharfbuzz-dev libfribidi-dev \
    libtiff5-dev libjpeg-dev libcairo2-dev \
    libxt-dev libx11-dev \
    && rm -rf /var/lib/apt/lists/*

RUN R -e "options(repos = c(CRAN = 'https://cloud.r-project.org')); \
    install.packages(c( \
        'dplyr', 'tidyr', 'data.table', 'magrittr', \
        'ggplot2', 'lattice', 'scales', 'Cairo', \
        'readr', 'readxl', 'writexl', 'jsonlite', 'xml2', \
        'MASS', 'survival', 'lubridate', 'stringr', 'glue' \
    ))"

ENV R_LIBS_USER=/usr/local/lib/R/site-library

FROM runtime-r AS app

ARG DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Keep the application layer thin so app-only changes do not invalidate runtime stages.
COPY docker/repl_server.py /opt/repl_server.py
COPY docker/ptc_server.py /opt/ptc_server.py
COPY docker/entrypoint.sh /opt/entrypoint.sh
RUN chmod +x /opt/repl_server.py /opt/ptc_server.py /opt/entrypoint.sh

COPY requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    grep -v '^docker==' /tmp/requirements.txt | \
    grep -v '^requests-unixsocket==' | \
    pip install --break-system-packages --ignore-installed -r /dev/stdin && \
    rm -f /tmp/requirements.txt

COPY src/ /app/src/
COPY dashboard/ /app/dashboard/
COPY skills/ /opt/skills/

RUN find / -path /proc -prune -o -path /sys -prune -o \
    \( -perm -4000 -o -perm -2000 \) -type f -exec chmod u-s,g-s {} + 2>/dev/null || true

EXPOSE 8000
CMD ["python3", "-m", "src.main"]
