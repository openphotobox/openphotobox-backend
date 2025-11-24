import os

from celery import Celery

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "openphotobox_backend.settings")

app = Celery("openphotobox_backend")

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

# Optional configuration for better performance
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Task routing
    task_routes={
        "metadata.tasks.process_asset_metadata": {"queue": "metadata"},
        "metadata.tasks.generate_clip_embedding": {"queue": "ai"},
        "people.tasks.detect_faces": {"queue": "ai"},
        "people.tasks.cluster_faces": {"queue": "ai"},
    },
    # Worker configuration
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    worker_disable_rate_limits=True,
)

# Celery Beat schedule for periodic tasks
app.conf.beat_schedule = {
    "revalidate-unconfirmed-faces": {
        "task": "people.tasks.revalidate_unconfirmed_faces",
        "schedule": 300.0,  # Every 5 minutes
    },
    "assign-unassigned-faces": {
        "task": "people.tasks.assign_faces_knn",
        "schedule": 600.0,  # Every 5 minutes
    },
}


@app.task(bind=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
