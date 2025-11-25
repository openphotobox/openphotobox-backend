FROM python:3.12-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install dependencies with CPU-only PyTorch (saves ~1.5GB)
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip && \
    # Use PyTorch CPU index for all installations
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt \
        --extra-index-url https://download.pytorch.org/whl/cpu

# Runtime stage
FROM python:3.12-slim

WORKDIR /app

# Install only runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    netcat-traditional

# Copy virtual environment from builder (much faster than copying site-packages)
COPY --from=builder --link /opt/venv /opt/venv

# Set PATH to use the virtual environment
ENV PATH="/opt/venv/bin:$PATH"

# Copy project files
COPY . .

EXPOSE 8000

# Default to production-ready Gunicorn; compose can override with runserver for dev
CMD ["gunicorn", "openphotobox_backend.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4", "--threads", "4", "--timeout", "120"]
