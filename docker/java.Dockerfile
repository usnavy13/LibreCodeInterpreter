# syntax=docker/dockerfile:1.4
# Java execution environment with BuildKit optimizations
FROM eclipse-temurin:25-jdk

# Install common tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Create library directory
RUN mkdir -p /opt/java/lib

# Download all JARs in a single layer (reduces layers, faster builds)
RUN cd /opt/java/lib && \
    # Apache Commons
    wget -q https://repo1.maven.org/maven2/org/apache/commons/commons-csv/1.10.0/commons-csv-1.10.0.jar && \
    wget -q https://repo1.maven.org/maven2/org/apache/commons/commons-lang3/3.14.0/commons-lang3-3.14.0.jar && \
    wget -q https://repo1.maven.org/maven2/org/apache/commons/commons-math3/3.6.1/commons-math3-3.6.1.jar && \
    wget -q https://repo1.maven.org/maven2/org/apache/commons/commons-collections4/4.4/commons-collections4-4.4.jar && \
    wget -q https://repo1.maven.org/maven2/org/apache/commons/commons-compress/1.25.0/commons-compress-1.25.0.jar && \
    wget -q https://repo1.maven.org/maven2/org/apache/commons/commons-text/1.11.0/commons-text-1.11.0.jar && \
    # Jackson JSON
    wget -q https://repo1.maven.org/maven2/com/fasterxml/jackson/core/jackson-core/2.16.0/jackson-core-2.16.0.jar && \
    wget -q https://repo1.maven.org/maven2/com/fasterxml/jackson/core/jackson-databind/2.16.0/jackson-databind-2.16.0.jar && \
    wget -q https://repo1.maven.org/maven2/com/fasterxml/jackson/core/jackson-annotations/2.16.0/jackson-annotations-2.16.0.jar && \
    # Apache POI (Excel)
    wget -q https://repo1.maven.org/maven2/org/apache/poi/poi/5.2.5/poi-5.2.5.jar && \
    wget -q https://repo1.maven.org/maven2/org/apache/poi/poi-ooxml/5.2.5/poi-ooxml-5.2.5.jar && \
    wget -q https://repo1.maven.org/maven2/org/apache/poi/poi-ooxml-lite/5.2.5/poi-ooxml-lite-5.2.5.jar && \
    wget -q https://repo1.maven.org/maven2/org/apache/xmlbeans/xmlbeans/5.2.0/xmlbeans-5.2.0.jar && \
    # Apache PDFBox
    wget -q https://repo1.maven.org/maven2/org/apache/pdfbox/pdfbox/3.0.1/pdfbox-3.0.1.jar && \
    wget -q https://repo1.maven.org/maven2/org/apache/pdfbox/fontbox/3.0.1/fontbox-3.0.1.jar && \
    # Google Guava
    wget -q https://repo1.maven.org/maven2/com/google/guava/guava/33.0.0-jre/guava-33.0.0-jre.jar && \
    # NEW: Gson (alternative JSON)
    wget -q https://repo1.maven.org/maven2/com/google/code/gson/gson/2.10.1/gson-2.10.1.jar && \
    # NEW: Joda-Time
    wget -q https://repo1.maven.org/maven2/joda-time/joda-time/2.12.5/joda-time-2.12.5.jar

# Create non-root user
RUN groupadd -r codeuser && useradd -r -g codeuser codeuser

# Set working directory
WORKDIR /mnt/data

# Ensure ownership of working directory
RUN chown -R codeuser:codeuser /mnt/data

# Switch to non-root user
USER codeuser

# Set environment variables with updated CLASSPATH
ENV JAVA_OPTS="-Xmx512m -Xms128m" \
    CLASSPATH="/mnt/data:/opt/java/lib/*"

# Default command with sanitized environment (include Java bin path)
ENTRYPOINT ["/usr/bin/env","-i","PATH=/opt/java/openjdk/bin:/usr/local/bin:/usr/bin:/bin","HOME=/tmp","TMPDIR=/tmp","CLASSPATH=/mnt/data:/opt/java/lib/*","JAVA_OPTS=-Xmx512m -Xms128m"]
CMD ["java", "--version"]
