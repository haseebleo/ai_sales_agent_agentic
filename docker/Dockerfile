# ── Build Stage ────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Runtime Stage ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# Runtime system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Create runtime directories
RUN mkdir -p /app/data /app/logs /app/kb

# Copy application source
COPY app/ ./app/
COPY main.py .
COPY kb/ ./kb/

# Non-root user for security
RUN useradd -m -u 1000 agentuser && chown -R agentuser:agentuser /app
USER agentuser

EXPOSE 8000

# Run indexing then start server
CMD ["sh", "-c", \
     "python -m app.rag.indexer && \
      uvicorn app.api.routes:app \
      --host 0.0.0.0 \
      --port 8000 \
      --workers 2 \
      --log-level info"]
