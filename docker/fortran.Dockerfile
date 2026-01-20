# syntax=docker/dockerfile:1.4
# Fortran execution environment with BuildKit optimizations
FROM ubuntu:24.04

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies and Fortran compiler
RUN apt-get update && apt-get install -y --no-install-recommends \
    gfortran-12 \
    gcc \
    g++ \
    make \
    cmake \
    libblas-dev \
    liblapack-dev \
    libnetcdf-dev \
    libhdf5-dev \
    build-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set gfortran-12 as the default fortran compiler
RUN update-alternatives --install /usr/bin/gfortran gfortran /usr/bin/gfortran-12 100 \
    && update-alternatives --install /usr/bin/f95 f95 /usr/bin/gfortran-12 100

# Create non-root user
RUN groupadd -g 1001 codeuser && \
    useradd -r -u 1001 -g codeuser codeuser

# Set working directory and ensure ownership
WORKDIR /mnt/data
RUN chown -R codeuser:codeuser /mnt/data

# Switch to non-root user
USER codeuser

# Set environment variables
ENV FORTRAN_COMPILER=gfortran \
    FC=gfortran \
    F77=gfortran \
    F90=gfortran \
    F95=gfortran

# Default command with sanitized environment
ENTRYPOINT ["/usr/bin/env","-i","PATH=/usr/local/bin:/usr/bin:/bin","HOME=/tmp","TMPDIR=/tmp","FORTRAN_COMPILER=gfortran","FC=gfortran","F77=gfortran","F90=gfortran","F95=gfortran"]
CMD ["gfortran", "--version"]
