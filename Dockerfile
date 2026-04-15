# ─────────────────────────────────────────────────────────────────────────────
# RCM Denial Management — Production Dockerfile
#
# Builds a container with everything needed:
#   - Python 3.11 + all pip packages (LangGraph, ChromaDB, NiceGUI, etc.)
#   - Tesseract OCR + poppler (for scanned PDF fallback)
#   - PyMuPDF (for digital PDF extraction — installed via pip)
#   - PostgreSQL client libs (for production DB option)
#
# Usage:
#   docker build -t rcm-denial .
#   docker run -p 8080:8080 -v $(pwd)/.env:/app/.env:ro rcm-denial
#   docker run rcm-denial process-batch /app/data/demo_denials.csv
#
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim AS base

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code and data
COPY pyproject.toml .
COPY src/ src/
COPY data/ data/
COPY scripts/ scripts/
COPY tests/ tests/
COPY .env.example .env.example

# Install the project package
RUN pip install --no-cache-dir -e .

# Create runtime directories
RUN mkdir -p output logs data/metrics data/chroma_db data/eob_pdfs

# Default env vars (override via .env mount or docker -e flags)
ENV ENV=production \
    DATABASE_TYPE=sqlite \
    OUTPUT_DIR=/app/output \
    LOG_DIR=/app/logs \
    DATA_DIR=/app/data \
    LOG_LEVEL=INFO \
    METRICS_EXPORT_AFTER_BATCH=true \
    WEB_PORT=8080

EXPOSE 8080

# Default: launch web UI
ENTRYPOINT ["rcm-denial"]
CMD ["web", "--host", "0.0.0.0"]
