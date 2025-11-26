"""
OpenTelemetry instrumentation configuration for OpenPhotobox.

This module sets up distributed tracing for Django, PostgreSQL, Redis, Celery,
and external HTTP requests. Traces are exported to a configurable OTLP endpoint
(e.g., Grafana Tempo).

Environment Variables:
    OTEL_ENABLED: Enable/disable tracing (default: true)
    OTEL_EXPORTER_OTLP_ENDPOINT: OTLP endpoint URL (e.g., http://tempo:4318)
    OTEL_SERVICE_NAME: Service name in traces (default: openphotobox-backend)
    OTEL_ENVIRONMENT: Environment tag (default: development)
"""

import logging
import os

logger = logging.getLogger(__name__)

# Global flag to prevent duplicate initialization
_telemetry_initialized = False


def setup_telemetry():
    """Initialize OpenTelemetry instrumentation."""
    global _telemetry_initialized

    # Prevent duplicate initialization
    if _telemetry_initialized:
        return

    # Check if tracing is enabled
    otel_enabled = os.environ.get("OTEL_ENABLED", "true").lower() in ("true", "1", "yes")
    if not otel_enabled:
        logger.info("OpenTelemetry tracing is disabled (OTEL_ENABLED=false)")
        _telemetry_initialized = True
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.celery import CeleryInstrumentor
        from opentelemetry.instrumentation.django import DjangoInstrumentor
        from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
        from opentelemetry.instrumentation.redis import RedisInstrumentor
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        # Get configuration from environment
        service_name = os.environ.get("OTEL_SERVICE_NAME", "openphotobox-backend")
        environment = os.environ.get("OTEL_ENVIRONMENT", "development")
        otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")

        if not otlp_endpoint:
            logger.warning(
                "OTEL_EXPORTER_OTLP_ENDPOINT not set. "
                "Tracing is enabled but spans will not be exported. "
                "Set OTEL_EXPORTER_OTLP_ENDPOINT to export traces (e.g., http://tempo:4318)"
            )
            return

        # Create resource with service metadata
        resource = Resource.create(
            {
                "service.name": service_name,
                "deployment.environment": environment,
            }
        )

        # Set up tracer provider
        provider = TracerProvider(resource=resource)
        trace.set_tracer_provider(provider)

        # Configure OTLP exporter (HTTP/protobuf)
        otlp_exporter = OTLPSpanExporter(
            endpoint=f"{otlp_endpoint}/v1/traces",
            timeout=10,  # 10 second timeout
        )

        # Use batch processor for better performance
        span_processor = BatchSpanProcessor(otlp_exporter)
        provider.add_span_processor(span_processor)

        # Instrument Django (views, middleware, templates)
        try:
            DjangoInstrumentor().instrument()
        except Exception as e:
            logger.warning(f"Failed to instrument Django: {e}")

        # Instrument PostgreSQL queries
        # Note: psycopg2-binary provides the same module as psycopg2
        # The Django instrumentor already traces DB queries, so this adds extra detail
        try:
            # Skip version check since psycopg2-binary doesn't register as psycopg2
            instrumentor = Psycopg2Instrumentor()
            if not instrumentor.is_instrumented_by_opentelemetry:
                instrumentor.instrument(
                    skip_dep_check=True,  # Skip dependency check for psycopg2-binary
                    enable_commenter=True,  # Add trace context as SQL comments
                    commenter_options={"db_driver": True, "db_framework": True},
                )
        except Exception as e:
            logger.debug(f"Psycopg2 instrumentation skipped: {e} (Django DB tracing still active)")

        # Instrument Redis operations
        try:
            RedisInstrumentor().instrument()
        except Exception as e:
            logger.warning(f"Failed to instrument Redis: {e}")

        # Instrument Celery tasks
        try:
            CeleryInstrumentor().instrument()
        except Exception as e:
            logger.warning(f"Failed to instrument Celery: {e}")

        # Instrument outbound HTTP requests
        try:
            RequestsInstrumentor().instrument()
        except Exception as e:
            logger.warning(f"Failed to instrument requests: {e}")

        logger.info(
            f"OpenTelemetry instrumentation initialized: "
            f"service={service_name}, environment={environment}, endpoint={otlp_endpoint}"
        )

        _telemetry_initialized = True

    except ImportError as e:
        logger.warning(f"OpenTelemetry dependencies not installed: {e}")
    except Exception as e:
        logger.error(f"Failed to initialize OpenTelemetry: {e}", exc_info=True)
