# OpenTelemetry Tracing Guide

This document provides detailed information about using OpenTelemetry distributed tracing with OpenPhotobox.

## Quick Start

### 1. Install Dependencies

After pulling the latest changes, install the new OpenTelemetry dependencies:

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

Set the following environment variables (or add to your `.env` file):

```bash
export OTEL_ENABLED=true
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
export OTEL_SERVICE_NAME=openphotobox-backend
export OTEL_ENVIRONMENT=development
```

### 3. Start Your Services

Start your application normally. Traces will be automatically collected:

```bash
# Django server
python manage.py runserver

# Celery worker (in another terminal)
python start_worker.py

# Celery beat (in another terminal)
python start_beat.py
```

## What Gets Traced

### Django Views
- HTTP method, path, status code
- Request duration
- Route pattern (e.g., `/api/assets/{id}/`)
- Query parameters and headers (configurable)

### Database Queries
- SQL query text (as span attributes)
- Query execution time
- Database name and operation type
- Connection pool metrics

### Celery Tasks
- Task name (e.g., `people.tasks.detect_faces`)
- Task arguments
- Execution time
- Success/failure status
- Queue name

### Redis Operations
- Command type (GET, SET, etc.)
- Execution time
- Cache hit/miss patterns

### HTTP Requests
- Outbound API calls
- Response status and timing

## Running with Tempo

### Using External Tempo Instance

Set the `OTEL_EXPORTER_OTLP_ENDPOINT` to point to your Tempo instance. You can do this via:

**Environment variable:**
```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://your-tempo-host:4318
python manage.py runserver
```

**Or in your `.env` file:**
```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo.your-network:4318
```

**Docker Compose:**

The `docker-compose.yaml` is already configured to read OTEL environment variables. Just set them in your `.env` file:

```bash
OTEL_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=http://your-tempo-host:4318
OTEL_SERVICE_NAME=openphotobox-backend
OTEL_ENVIRONMENT=production
```

Then start your services:

```bash
docker-compose up
```

## Viewing Traces in Grafana

1. Open Grafana at `http://localhost:3001`
2. Go to **Configuration** → **Data Sources** → **Add data source**
3. Select **Tempo**
4. Set URL to `http://tempo:3200` (or `http://localhost:3200` if running locally)
5. Click **Save & Test**

### Exploring Traces

1. Go to **Explore** (compass icon in sidebar)
2. Select **Tempo** from the data source dropdown
3. Query options:
   - **Search by Service Name**: `openphotobox-backend`
   - **Search by Span Name**: e.g., `GET /api/assets/`
   - **Search by Trace ID**: Copy from logs or other traces

### Common Queries

**Find slow database queries:**
```
{service.name="openphotobox-backend" && span.db.system="postgresql" && duration > 100ms}
```

**Find failing requests:**
```
{service.name="openphotobox-backend" && status.code="ERROR"}
```

**Find specific view:**
```
{service.name="openphotobox-backend" && http.route="/api/assets/{id}/"}
```

## Advanced Configuration

### Custom Spans

Add custom instrumentation to your code:

```python
from opentelemetry import trace

tracer = trace.get_tracer(__name__)

def my_function():
    with tracer.start_as_current_span("my_custom_operation") as span:
        # Your code here
        span.set_attribute("custom.attribute", "value")
        # ... do work ...
        span.add_event("Something interesting happened")
```

### Adding Span Attributes

Add context to existing spans:

```python
from opentelemetry import trace

# In a Django view
def my_view(request):
    span = trace.get_current_span()
    span.set_attribute("user.id", request.user.id)
    span.set_attribute("album.id", album_id)
    # ... rest of view logic ...
```

### Sampling

To reduce trace volume in production, configure sampling in `tracing.py`:

```python
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

# Sample 10% of traces
sampler = TraceIdRatioBased(0.1)
provider = TracerProvider(resource=resource, sampler=sampler)
```

## Performance Considerations

### Overhead

OpenTelemetry adds minimal overhead:
- ~1-2ms per traced operation
- Traces are batched and exported asynchronously
- No blocking on export failures

### Production Recommendations

1. **Use sampling**: Sample 1-10% of traces in high-traffic environments
2. **Filter sensitive data**: Don't log passwords, tokens, or PII in span attributes
3. **Monitor export queue**: Watch for backpressure if Tempo is slow/unavailable
4. **Set timeouts**: The default OTLP exporter timeout is 10 seconds

### Disabling in Development

If tracing adds unwanted noise during local development:

```bash
export OTEL_ENABLED=false
python manage.py runserver
```

## Troubleshooting

### No traces appearing in Tempo

1. Check that `OTEL_EXPORTER_OTLP_ENDPOINT` is set correctly
2. Verify Tempo is accepting connections: `curl http://localhost:4318/v1/traces`
3. Check application logs for OpenTelemetry errors
4. Ensure Tempo's OTLP receiver is enabled in `tempo.yaml`

### High latency after enabling tracing

1. Check Tempo connection latency: the default timeout is 10 seconds
2. Enable sampling to reduce trace volume
3. Verify network connectivity to Tempo

### Missing database query details

Database query text is included as span attributes. If missing:
1. Check that `enable_commenter=True` in `Psycopg2Instrumentor().instrument()`
2. Verify PostgreSQL connection is being instrumented

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `OTEL_ENABLED` | `true` | Enable/disable tracing |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | None | OTLP endpoint URL (required for export) |
| `OTEL_SERVICE_NAME` | `openphotobox-backend` | Service name in traces |
| `OTEL_ENVIRONMENT` | `development` | Environment tag (dev/staging/prod) |

## Resources

- [OpenTelemetry Python Docs](https://opentelemetry.io/docs/instrumentation/python/)
- [Grafana Tempo Docs](https://grafana.com/docs/tempo/latest/)
- [OTLP Specification](https://opentelemetry.io/docs/specs/otel/protocol/)
