# syntax=docker/dockerfile:1.4
# PHP execution environment with BuildKit optimizations
FROM php:8.5-cli

# Install system dependencies and PHP extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    libzip-dev \
    libpng-dev \
    libjpeg-dev \
    libfreetype6-dev \
    libonig-dev \
    libxml2-dev \
    unzip \
    git \
    && docker-php-ext-configure gd --with-freetype --with-jpeg \
    && docker-php-ext-install -j$(nproc) \
        xml \
        zip \
        gd \
        mbstring \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Composer
RUN curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer

# Create non-root user
RUN groupadd -g 1001 codeuser && \
    useradd -r -u 1001 -g codeuser codeuser

# Create global composer directory and set permissions
RUN mkdir -p /opt/composer/global && \
    chown -R codeuser:codeuser /opt/composer

# Switch to non-root user for package installation
USER codeuser

# Set composer home directory
ENV COMPOSER_HOME=/opt/composer/global

# Pre-install PHP packages globally with cache mount
RUN --mount=type=cache,target=/opt/composer/global/cache,uid=1001,gid=1001 \
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

# Switch back to root to set up directories and final permissions
USER root

# Set working directory and ensure ownership
WORKDIR /mnt/data
RUN chown -R codeuser:codeuser /mnt/data

# Switch to non-root user for execution
USER codeuser

# Set environment variables
ENV PATH="/opt/composer/global/vendor/bin:${PATH}" \
    PHP_INI_SCAN_DIR="/usr/local/etc/php/conf.d"

# Default command with sanitized environment
ENTRYPOINT ["/usr/bin/env","-i","PATH=/opt/composer/global/vendor/bin:/usr/local/bin:/usr/bin:/bin","HOME=/tmp","TMPDIR=/tmp","COMPOSER_HOME=/opt/composer/global","PHP_INI_SCAN_DIR=/usr/local/etc/php/conf.d"]
CMD ["php", "-a"]
