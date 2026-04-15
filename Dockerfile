# ─────────────────────────────────────────────────────────────────────────────
# RCM Denial Management — Production Dockerfile
#
# Builds a container that can run either:
#   CLI:  docker run rcm-denial process-batch /data/claims.csv
#   Web:  docker run -p 8080:8080 rcm-denial web --host 0.0.0.0
#
# Build:
#   docker build -t rcm-denial .
#
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim AS base

# System dependencies for OCR + PDF
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer caching)
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[web]" 2>/dev/null || true

# Copy source code
COPY src/ src/
COPY data/ data/
COPY scripts/ scripts/
COPY tests/ tests/

# Install the package
RUN pip install --no-cache-dir -e ".[web]"

# Create directories for runtime data
RUN mkdir -p output logs data/metrics data/chroma_db

# Default env vars (override via docker run -e or .env mount)
ENV ENV=production \
    DATABASE_TYPE=sqlite \
    OUTPUT_DIR=/app/output \
    LOG_DIR=/app/logs \
    DATA_DIR=/app/data \
    LOG_LEVEL=INFO \
    METRICS_EXPORT_AFTER_BATCH=true

# Expose web UI port
EXPOSE 8080

# Default: launch web UI (override with CLI commands)
ENTRYPOINT ["rcm-denial"]
CMD ["web", "--host", "0.0.0.0", "--port", "8080"]
