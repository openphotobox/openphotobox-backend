#!/usr/bin/env python
"""
Convenience script to start Celery Beat scheduler.

This scheduler runs periodic tasks defined in openphotobox_backend/celery.py beat_schedule.

Examples:
  python start_beat.py
  python start_beat.py --loglevel debug

Notes:
  - Only run ONE beat instance per deployment to avoid duplicate task execution.
  - The beat scheduler requires workers to be running to actually process scheduled tasks.
"""

import argparse
import os
import sys
from pathlib import Path

import django

# Add the backend directory to Python path
backend_dir = Path(__file__).parent
sys.path.insert(0, str(backend_dir))

# Set up Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "openphotobox_backend.settings")
django.setup()

# Import and start Celery (must occur after django.setup for settings-dependent initialization)
from openphotobox_backend.celery import app  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Start Celery Beat scheduler")
    parser.add_argument("--loglevel", default=os.environ.get("CELERY_LOGLEVEL", "info"), help="Logging level")

    args = parser.parse_args()

    print("Starting Celery Beat scheduler...")
    print(f"  Log level   : {args.loglevel}")
    print("Make sure Redis is running on localhost:6379")
    print("Press Ctrl+C to stop the scheduler")
    print("-" * 50)

    beat = app.Beat(loglevel=args.loglevel.upper())
    beat.run()


if __name__ == "__main__":
    main()
