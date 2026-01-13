# syntax=docker/dockerfile:1.4
# R execution environment with BuildKit optimizations
FROM r-base:4.4.3

# Install system dependencies for R packages (including Cairo)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcurl4-openssl-dev \
    libssl-dev \
    libxml2-dev \
    libfontconfig1-dev \
    libharfbuzz-dev \
    libfribidi-dev \
    libfreetype6-dev \
    libpng-dev \
    libtiff5-dev \
    libjpeg-dev \
    libcairo2-dev \
    libxt-dev \
    libx11-dev \
    && rm -rf /var/lib/apt/lists/*

# Install all R packages in a single layer using Posit Package Manager
# - amd64: Downloads pre-compiled binaries (~5 min)
# - arm64: Compiles from source but single layer avoids redundant dependency builds
RUN R -e "options(repos = c(CRAN = 'https://packagemanager.posit.co/cran/__linux__/bookworm/latest')); \
    install.packages(c( \
        'dplyr', 'tidyr', 'data.table', 'magrittr', \
        'ggplot2', 'lattice', 'scales', 'Cairo', \
        'readr', 'readxl', 'writexl', 'jsonlite', 'xml2', \
        'MASS', 'survival', 'lubridate', 'stringr', 'glue' \
    ))"

# Create non-root user
RUN groupadd -g 1001 codeuser && \
    useradd -r -u 1001 -g codeuser codeuser

# Set working directory and ensure ownership
WORKDIR /mnt/data
RUN chown -R codeuser:codeuser /mnt/data

# Switch to non-root user
USER codeuser

# Set environment variables
ENV R_LIBS_USER=/usr/local/lib/R/site-library

# Default command with sanitized environment
ENTRYPOINT ["/usr/bin/env","-i","PATH=/usr/local/bin:/usr/bin:/bin","HOME=/tmp","TMPDIR=/tmp","R_LIBS_USER=/usr/local/lib/R/site-library"]
CMD ["R", "--version"]
