# OpenPhotobox Backend

Django REST Framework backend for the OpenPhotobox family photo management system.

## Features

- **Asset Management**: Store and manage photos/videos with local filesystem storage
- **Face Recognition**: Detect faces and assign them to people
- **Albums**: Organize photos into albums
- **Sharing**: Create public share links for albums or people
- **Search**: CLIP-based semantic search capabilities
- **Upload Batches**: Track and manage bulk uploads

## Quick Start

### 1. Install Dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt
pre-commit install  # sets up git hooks
```

### 2. Set Up Storage

OpenPhotobox uses local filesystem storage for photos. You can set up storage in two ways:

#### Option A: Frontend Setup (Recommended)
The frontend will prompt users to configure storage on first run:

```javascript
// POST /api/storage/setup/
{
  "path": "/home/user/photos"
}
```

#### Option B: Command Line Setup
Or set it up from the command line:

```bash
# Interactive setup
python manage.py setup_local_storage

# Non-interactive setup
python manage.py setup_local_storage --path /var/photos --default
```

Both methods:
- Create the storage configuration
- Set up originals/ and thumbnails/ subdirectories
- Make the storage ready for uploads

### 3. Storage Organization

Photos are organized by date in the following structure:

```
/storage_path/
  originals/
    2024/
      11/
        24/
          {uuid}.jpg
          {uuid}.png
  thumbnails/
    2024/
      11/
        24/
          {uuid}_md.jpg
          {uuid}_sm.jpg
          {uuid}_lg.jpg
```

### 4. Environment Variables

You can optionally configure the default storage path via environment variable:

```bash
export STORAGE_PATH=/path/to/your/photos
```

Or add it to your `.env` file:

```
STORAGE_PATH=/var/photos
DATABASE_URL=postgresql://archive:archive@localhost:5432/archive
REDIS_URL=redis://localhost:6379/0
```

## OpenTelemetry / Distributed Tracing

OpenPhotobox includes built-in OpenTelemetry instrumentation to help you understand application performance, view latency, database query times, and background task execution.

### What Gets Traced

- **Django Views**: HTTP request/response cycles with status codes and route patterns
- **Database Queries**: PostgreSQL query execution times with query text as span attributes
- **Redis Operations**: Cache hits/misses and timing
- **Celery Tasks**: Background task execution (face detection, metadata processing, etc.)
- **HTTP Requests**: Outbound API calls (if any)

### Configuration

Set these environment variables to configure tracing:

```bash
# Enable/disable tracing (default: true)
OTEL_ENABLED=true

# OTLP endpoint for your tracing backend (e.g., Grafana Tempo)
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318

# Service name that appears in traces
OTEL_SERVICE_NAME=openphotobox-backend

# Environment tag (development, staging, production)
OTEL_ENVIRONMENT=development
```

### Example: Using with Grafana Tempo

If you have Tempo running locally or in Docker:

```bash
# For Tempo with OTLP HTTP receiver on port 4318
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318

# Or for Docker Compose (service name)
export OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4318
```

Then start your services normally:

```bash
# Django server will export traces
python manage.py runserver

# Celery workers will export traces
python start_worker.py

# Celery beat will export traces
python start_beat.py
```

### Disabling Tracing

To completely disable tracing:

```bash
export OTEL_ENABLED=false
```

### Viewing Traces

With Tempo + Grafana:
1. Open Grafana (typically `http://localhost:3000`)
2. Go to Explore → Select Tempo data source
3. Search for traces by service name or trace ID
4. Analyze view latency, slow queries, and task execution times

## Storage Configuration

### Quick Setup

The simplest way to configure storage is via the API:

```bash
# Check if storage is configured
curl http://localhost:8000/api/storage/status/

# Configure storage (requires admin auth)
curl -X POST http://localhost:8000/api/storage/setup/ \
  -H "Authorization: Token YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"path": "/home/user/photos"}'
```

### Multiple Storage Locations (Future)

The current implementation uses a single storage location. Support for multiple "libraries" (different drives/locations) can be added later by creating additional storage backends via the advanced `/api/storage-backends/` API.

## Background Tasks with Celery

OpenPhotobox uses Celery for asynchronous task processing (face detection, metadata extraction, etc.) and Celery Beat for periodic tasks (revalidating face assignments).

### Running with Docker Compose

```bash
docker-compose up
```

This starts:
- `backend`: Django web server
- `worker`: Celery worker (processes queued tasks)
- `beat`: Celery Beat scheduler (triggers periodic tasks)
- `db`, `redis`, `minio`: Supporting services

### Running Locally

You need to run THREE services in separate terminals:

**Terminal 1 - Django Server:**
```bash
python manage.py runserver
```

**Terminal 2 - Celery Worker:**
```bash
# Default worker (all queues)
python start_worker.py

# Or specialized workers:
python start_worker.py --preset metadata  # For EXIF/metadata processing
python start_worker.py --preset ai        # For face detection & CLIP
```

**Terminal 3 - Celery Beat (Required for periodic tasks):**
```bash
python start_beat.py
```

**Important:** Without Celery Beat running, periodic tasks like `revalidate_unconfirmed_faces` won't execute automatically.

### Periodic Tasks

The following tasks run automatically when Beat is running:
- **revalidate_unconfirmed_faces**: Runs every 15 minutes to improve face assignments as users confirm faces

## Development Tooling

This project uses Ruff (lint + format), Mypy (static typing), and Pre-Commit hooks for consistent code quality.
